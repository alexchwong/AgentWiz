/* The form and runner use the same reviewed Workshop operations. Exact values stay as text. */
"use strict";
window.createWorkflowPage = function ({ h, api, wsApi, dialog, copyControl, contracts, openBlock, openServers }) {
  const panel = document.getElementById("panel-workflows");
  const status = h("p", {role:"status", "data-test":"wf-status"});
  const main = h("div");
  panel.append(h("h2", {text:"Workflows"}), status, main);
  let draft, token, dirty = false, names = [], ports = {}, sources = {}, selections = {}, layout = {};
  let examples = [], selected = new Set(), inputs = {}, view = "design", plan, planRequest;
  let busy = false, job, pendingStart, timer, generation = 0, rebind = null, contractEditors = [], canvasOpen = false;
  let releaseAcknowledgements={}, releaseSubmitting=false, releasePanelToken=null, releasePanelElement=null;
  const test = (name) => ({"data-test":"wf-"+name});
  const route = (name, action = "") => "workflows/" + encodeURIComponent(name) + action;
  const report = (message) => {status.textContent = message;};
  const issues = (rows = []) => h("ul", {}, rows.map(row => h("li", {text:(row.location || "") + " · " + (row.message || row.code)})));
  function replace(target, ...children) {
    const wrapper = h("div", {}, ...children);
    target.replaceChildren(...wrapper.childNodes);
  }
  function error(e) {
    report([e.message, ...(e.body?.issues || []).map(row => row.location + " · " + row.message)].join(" "));
  }
  const button = (label, fn, id, disabled = false) => h("button", {
    type:"button", text:label, ...test(id), disabled, onclick:async () => {try {await fn();} catch(e) {error(e);}}
  });
  const field = (label, value, fn, multi = false) => {
    const control = h(multi ? "textarea" : "input", {value:value ?? "", ...test(label.toLowerCase().replaceAll(" ", "-"))});
    control.oninput = () => fn(control.value,control);
    return h("label", {class:"ws-field"}, label, control);
  };
  function select(label, options, value, fn, id) {
    const el = h("select", {...test(id || "choice")}, options.map(([key, text]) => new Option(text, key)));
    el.value = value ?? "";
    el.onchange = () => fn(el.value);
    return h("label", {class:"ws-field"}, label, el);
  }
  const toggle = (label, checked, fn) => {
    const input = h("input", {type:"checkbox", checked, "aria-label":label});
    input.onchange = () => fn(input.checked);
    return h("label", {}, input, " " + label);
  };
  function changed() {
    dirty = true;
    invalidatePlan();
    main.querySelector('[data-test="wf-save"]')?.removeAttribute("disabled");
    const identity = main.querySelector('[data-test="wf-identity"]');
    if (identity) identity.textContent = (draft.name || "New workflow") + " · Unsaved";
  }
  function invalidatePlan() {
    plan = planRequest = null;
    main.querySelector('[data-test="wf-review"]')?.replaceChildren();
    main.querySelector('[data-test="wf-run"]')?.setAttribute("disabled", "");
  }
  const entries = (obj) => Object.entries(obj || {});
  async function exclusive(fn) {
    if (busy) return;
    busy = true;
    main.inert = true;
    try {return await fn();}
    finally {busy = false; main.inert = false;}
  }
  function clearRun() {
    invalidatePlan();
    releasePanelToken=releasePanelElement=null;
    job = null;
    clearTimeout(timer);
    generation++;
  }
  function load(data) {
    clearRun();
    draft = data?.editor || {name:"", purpose:"", inputs:{}, outputs:{}, constants:{}, nodes:{}};
    token = data?.file_token ?? null;
    sources = data?.sources || {};
    ports = data?.node_ports || {};
    selections = {};
    layout = data?.layout || {};
    examples = data?.examples || [];
    selected = new Set();
    inputs = {};
    rebind = null;
    dirty = !data;
    view = "design";
    render();
    if (data?.issues?.length) main.querySelector('[data-test="wf-design"]')?.prepend(issues(data.issues));
  }
  async function refresh() {names = (await wsApi("workflows")).workflows;}
  async function leave() {
    if (!dirty) return true;
    const answer = await dialog("Unsaved workflow", h("p", {text:"Save the changes before opening another workflow?"}), ["Save","Discard","Stay"]);
    if (answer === "Save") return await save();
    return answer === "Discard";
  }
  async function open(name, create = false) {
    await exclusive(async () => {
      if (!await leave()) {render(); return;}
      if(name||create)load(name ? await wsApi(route(name)) : null);
      else clearSelection();
      report("");
    });
  }
  function clearSelection() {
    clearRun();draft=null;token=null;dirty=false;render();
  }
  function request() {
    return {name:draft.name, file_token:token, editor:structuredClone(draft), selections, layout, examples};
  }
  const planFor = (action, request) => wsApi("plans", {action:"workflow_"+action, request});
  const reviewBody = (request, plan) => ({request, expected_plan:plan.expected_plan,
    confirmations:plan.required_confirmations.map(({kind,digest}) => ({kind,digest}))});
  async function save() {
    if (!draft || !dirty) return true;
    const invalid=main.querySelector("input:invalid");
    if(invalid){report(invalid.validationMessage);return false;}
    for (const editor of contractEditors) {
      await editor.ready();
      if (!editor.valid()) {report("Correct the workflow contracts before saving.");return false;}
    }
    const action = rebind ? "rebind" : "save";
    const body = request();
    const preview = await planFor(action, body);
    const content = h("div", {}, issues(preview.descriptor.issues),
      (preview.descriptor.changes || []).map(change => h("p", {text:change.node + " · " + change.before.title + " → " + change.after.title +
        " · " + entries(change.ports).flatMap(([side,row]) => entries(row).filter(([,items]) => items.length)
          .map(([kind,items]) => side + " " + kind + ": " + items.join(", "))).join("; ")})),
      h("p", {text:"Saved examples and constants can contain protected values in unencrypted local files."}));
    if (await dialog(rebind ? "Review node rebind" : "Review workflow save", content, ["Confirm","Cancel"]) !== "Confirm") return false;
    await wsApi(route(body.name, "/"+action), reviewBody(body, preview));
    await refresh();
    load(await wsApi(route(body.name)));
    report("Workflow saved.");
    return true;
  }
  async function removeWorkflow() {
    const body = {name:draft.name, file_token:token};
    const preview = await planFor("delete", body);
    if (await dialog("Delete workflow", h("p", {text:"Delete " + body.name + "? Saved run evidence is kept."}), ["Delete","Cancel"]) !== "Delete") return;
    await wsApi(route(body.name, "/delete"), reviewBody(body, preview));
    await refresh();
    clearSelection();
    report("Workflow deleted.");
  }
  async function newKey(title, existing) {
    let key = "";
    const content = h("div", {}, field("Identifier", "", v => key = v));
    const msg = h("p", {role:"alert"});
    content.append(msg);
    const choice = await dialog(title, content, ["Add","Cancel"], action => {
      if (action !== "Add") return true;
      if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) && !Object.hasOwn(existing,key)) return true;
      msg.textContent = "Use a unique identifier starting with a letter or underscore.";
      return false;
    });
    return choice === "Add" ? key : null;
  }
  function detach(match) {
    for (const output of Object.values(draft.outputs)) if (match(output.binding)) delete output.binding;
    for (const node of Object.values(draft.nodes)) for (const [name,binding] of entries(node.bindings))
      if (match(binding)) delete node.bindings[name];
  }
  function wouldCycle(consumer, source) {
    if (!consumer) return false;
    const pending = [source], seen = new Set();
    while (pending.length) {
      const node = pending.pop();
      if (node === consumer) return true;
      if (seen.has(node)) continue;
      seen.add(node);
      for (const value of Object.values(draft.nodes[node]?.bindings || {}))
        if (value.kind === "node") pending.push(value.node);
    }
    return false;
  }
  function setNodeBinding(node, port, binding) {
    if (binding?.kind === "node" && wouldCycle(node, binding.node)) {
      report("That connection would create a cycle.");
      return false;
    }
    const bindings = draft.nodes[node].bindings;
    if (binding) putKey(bindings, port, binding);
    else delete bindings[port];
    changed();
    return true;
  }
  function positions() {
    const points = new Map();
    const occupied = new Set(Object.values(layout).map(({x,y}) => x+","+y));
    for (const [index,node] of Object.keys(draft.nodes).sort().entries()) {
      if (Object.hasOwn(layout,node)) {
        points.set(node,layout[node]);
        continue;
      }
      let point, slot=index;
      do {
        point={x:(slot%4)*220,y:Math.floor(slot/4)*130};
        slot++;
      } while (occupied.has(point.x+","+point.y));
      occupied.add(point.x+","+point.y);
      points.set(node,point);
    }
    return points;
  }
  function setPosition(id,x,y) {
    if (![x,y].every(n=>Number.isSafeInteger(n)&&Math.abs(n)<=100000)) {
      report("Enter whole coordinates from −100000 to 100000.");
      return false;
    }
    putKey(layout,id,{x,y});
    changed();
    return true;
  }
  function layoutForm(id, point) {
    let x=String(point.x), y=String(point.y);
    return h("details",{},h("summary",{text:"Canvas position"}),
      field("X coordinate",x,v=>x=v),field("Y coordinate",y,v=>y=v),
      button("Set position",()=>{
        if (!/^-?\d+$/.test(x)||! /^-?\d+$/.test(y)) {
          report("Enter whole canvas coordinates.");return;
        }
        if(setPosition(id,Number(x),Number(y)))render();
      },"set-position"));
  }
  function compatible(from, to) {
    if(!from?.value_types||!to?.value_types)return true;
    return from.value_types.some(type=>to.value_types.includes(type)||
      type==="integer"&&to.value_types.includes("number")||type==="number"&&to.value_types.includes("integer"));
  }
  function gatePorts(config) {
    let contract;
    try {contract=JSON.parse(config.contract);}catch {contract={format:"json"};}
    const format=contract.format||"text";
    return {kind:"gate",context_local_only:false,inputs:{value:{format,local_only:false},scan:{format:"json",local_only:false}},
      outputs:{released:{format,local_only:false}}};
  }
  async function addGate() {
    const id=await newKey("Add Release Gate",draft.nodes);
    if(!id)return;
    const gate={kind:"release",title:"Review redacted text",contract:'{"format":"text"}',
      max_release_wait_seconds:1800,assessment:{enabled:false}};
    putKey(draft.nodes,id,{gate,bindings:{}});putKey(ports,id,gatePorts(gate));
    changed();render();
  }
  function gateForm(id,node,group) {
    const gate=node.gate;
    group.append(field("Gate title",gate.title,v=>{gate.title=v;changed();}),
      field("Review wait seconds",gate.max_release_wait_seconds??1800,v=>{
        gate.max_release_wait_seconds=/^\d+$/.test(v)?Number(v):null;changed();}));
    const editor=contracts.editor({test:"wf-gate-contract",text:gate.contract,onChange:text=>{
      gate.contract=text;putKey(ports,id,gatePorts(gate));changed();}});
    contractEditors.push(editor);
    group.append(h("details",{},h("summary",{text:"Released value contract"}),editor.el));
    gate.assessment??={enabled:false};
    group.append(toggle("Local AI assessment",gate.assessment.enabled,v=>{gate.assessment.enabled=v;changed();render();}));
    if(gate.assessment.enabled) {
      const roleBox=h("div");
      const choice=select("Assessment role",[[gate.assessment.role||"","Loading roles…"]],gate.assessment.role||"",v=>{
        gate.assessment.role=v;changed();},"assessment-role");
      roleBox.append(choice);group.append(roleBox,h("p",{text:"Assign this role to a trusted local model in Roles. Findings remain local."}));
      const marker=generation;
      api("/api/state").then(state=>{
        if(marker!==generation||!roleBox.isConnected)return;
        const names=new Set([...Object.keys(state.roles.definitions),...Object.keys(state.roles.map),...(gate.assessment.role?[gate.assessment.role]:[])]);
        replace(roleBox,select("Assessment role",[["","Choose a role"],...[...names].sort().map(name=>[
          name,name+" · "+(state.roles.map[name]||"Unassigned")])],gate.assessment.role||"",v=>{
          gate.assessment.role=v;changed();},"assessment-role"));
      }).catch(error);
    }
    group.append(h("p",{text:"Bind value to the candidate. Optional scan accepts a local-only identifier mapping. The original remains local-only after release."}));
  }
  function restrictedSources() {
    const key=b=>b?JSON.stringify(b.kind==="node"?["node",b.node,b.port]:b.kind==="input"?["input",b.port]:["constant",b.name]):"";
    const marked=new Set();
    for(const [name,row] of entries(draft.inputs))if(row.local_only)marked.add(JSON.stringify(["input",name]));
    for(const [name,row] of entries(draft.constants))if(row.local_only)marked.add(JSON.stringify(["constant",name]));
    for(const row of Object.values(draft.outputs))if(row.local_only&&row.binding)marked.add(key(row.binding));
    for(const [node,row] of entries(draft.nodes)) {
      for(const [name,port] of entries(ports[node]?.outputs))if(port.local_only)marked.add(JSON.stringify(["node",node,name]));
      if(!row.gate)for(const [name,port] of entries(ports[node]?.inputs))if(port.local_only&&row.bindings[name])marked.add(key(row.bindings[name]));
    }
    let previous=-1;
    while(previous!==marked.size) {
      previous=marked.size;
      for(const [node,row] of entries(draft.nodes))if(!row.gate&&(ports[node]?.context_local_only||Object.values(row.bindings).some(b=>marked.has(key(b)))))
        for(const name of Object.keys(ports[node]?.outputs||{}))marked.add(JSON.stringify(["node",node,name]));
    }
    return {marked,key};
  }
  function releasePanel(review) {
    if(releasePanelToken===review.token&&releasePanelElement)return releasePanelElement;
    const box=h("section",{...test("release-panel"),"data-gate":review.gate},h("h3",{text:"Release Gate · "+review.title}),
      h("p",{text:"Review the complete value and its destinations. Release permits downstream processing; this is not an anonymity certificate or a complete HTTP request preview."}),
      h("pre",{class:"wf-release-payload",text:review.payload,...test("release-payload")}),
      copyControl("wf-copy-release",()=>review.payload,null),
      h("p",{text:"Review expires "+new Date(review.expires_at*1000).toLocaleString()}));
    box.append(h("h4",{text:"Downstream destinations"}),entries(review.destinations).map(([node,row])=>h("p",{text:
      node+" · "+row.kind+" · "+row.url+" · "+(row.model||row.tool||"")})));
    const check=review.identifier_check, assessment=review.assessment;
    box.append(h("h4",{text:"Identifier check"}),h("p",{text:check.status==="not_configured"?"Identifier check not configured":
      check.hits.length?check.hits.length+" flagged matches":"No mapped originals found. This is not proof of anonymity."}),
      check.hits.map(hit=>h("p",{text:hit.identifier+" · "+JSON.stringify(hit.path)+" · "+hit.part+" · "+hit.start+"–"+hit.end})),
      h("h4",{text:"Local AI assessment"}),h("p",{...test("release-assessment"),"data-status":assessment.status,text:
        assessment.status==="not_configured"?"Local AI assessment not configured":assessment.status==="unavailable"?
        "Assessment unavailable · "+assessment.reason:assessment.findings.length?"Review the concerns below":"No concerns identified by the local model. This is not proof of anonymity."}),
      assessment.findings.map(item=>h("p",{text:item.concern+" · "+item.location+" · "+item.explanation})));
    if(releaseAcknowledgements.token!==review.token)releaseAcknowledgements={token:review.token,identifiers:false,assessment:false};
    const needIds=check.hits.length>0, needAI=assessment.status==="unavailable"||assessment.findings.length>0;
    const enabled=()=>!releaseSubmitting&&(!needIds||releaseAcknowledgements.identifiers)&&(!needAI||releaseAcknowledgements.assessment);
    const decide=async decision=>{
      releaseSubmitting=true;
      box.querySelectorAll("button").forEach(control=>control.disabled=true);
      try {
        await wsApi("workflow-jobs/"+encodeURIComponent(job.id)+"/release",{
          token:review.token,payload_digest:review.payload_digest,executable_digest:review.executable_digest,
          scope_digest:review.scope_digest,checks_digest:review.checks_digest,decision,
          acknowledge_identifiers:releaseAcknowledgements.identifiers,acknowledge_assessment:releaseAcknowledgements.assessment});
      } finally {releaseSubmitting=false;box.querySelectorAll("button").forEach(control=>control.disabled=false);release.disabled=!enabled();}
    };
    const release=button("Review and release",()=>decide("release"),"release-approve",!enabled());
    if(needIds)box.append(toggle("I have reviewed the flagged identifier matches",releaseAcknowledgements.identifiers,v=>{
      releaseAcknowledgements.identifiers=v;release.disabled=!enabled();}));
    if(needAI)box.append(toggle("I have reviewed the AI concerns or assessment unavailability",releaseAcknowledgements.assessment,v=>{
      releaseAcknowledgements.assessment=v;release.disabled=!enabled();}));
    const reject=button("Reject",()=>decide("reject"),"release-reject",releaseSubmitting);
    box.append(release,reject);
    releasePanelToken=review.token;releasePanelElement=box;
    return box;
  }
  function canvas(points) {
    const region=h("details",{open:canvasOpen},h("summary",{text:"Canvas"}),
      h("p",{text:"Move cards by their headers. Drag an output onto an input to connect them. Use Nodes for keyboard wiring, removal and positioning."}));
    region.ontoggle=()=>{canvasOpen=region.open;};
    if(!points.size)return region;
    const pointList=[...points.values()];
    const originX=Math.min(0,...pointList.map(p=>p.x)), originY=Math.min(0,...pointList.map(p=>p.y));
    const extentX=Math.max(...pointList.map(p=>p.x-originX))+220;
    const extentY=Math.max(...pointList.map(p=>p.y-originY))+145;
    const scroll=h("div",{class:"wf-canvas-scroll",tabindex:"0","aria-label":"Workflow canvas"});
    const surface=h("div",{class:"wf-canvas",...test("canvas")});
    surface.style.width=extentX+"px";
    surface.style.height=extentY+"px";
    const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("aria-hidden","true");
    surface.append(svg);
    const cards=new Map();
    let connection=null;
    function edges() {
      svg.replaceChildren();
      const restriction=restrictedSources();
      for (const [target,row] of entries(draft.nodes)) for (const binding of Object.values(row.bindings)) {
        if (binding.kind!=="node"||!cards.has(binding.node))continue;
        const a=points.get(binding.node), b=points.get(target);
        const line=document.createElementNS(svg.namespaceURI,"line");
        line.setAttribute("x1",String(a.x-originX+195));line.setAttribute("y1",String(a.y-originY+50));
        line.setAttribute("x2",String(b.x-originX));line.setAttribute("y2",String(b.y-originY+50));
        const restricted=restriction.marked.has(restriction.key(binding));
        line.dataset.localOnly=String(restricted);
        line.style.stroke=restricted?"#b45309":"#15803d";
        const title=document.createElementNS(svg.namespaceURI,"title");title.textContent=restricted?"Local only":draft.nodes[binding.node]?.gate?"Unrestricted after approval":"Unrestricted";line.append(title);
        svg.append(line);
      }
    }
    for(const [id,point] of points) {
      const header=h("div",{class:"wf-canvas-handle",text:(draft.nodes[id].gate?"Release Gate · ":"")+id,...test("canvas-move-"+id)});
      const card=h("div",{class:"wf-canvas-node",...test("canvas-node-"+id)},header);
      cards.set(id,card);
      const place=({x,y})=>{
        card.style.left=(x-originX+10)+"px";
        card.style.top=(y-originY+10)+"px";
      };
      place(point);
      let moving=null;
      header.onpointerdown=e=>{
        if(e.button!==0)return;
        const p=points.get(id);
        moving={startX:e.clientX,startY:e.clientY,x:p.x,y:p.y};
        header.setPointerCapture(e.pointerId);
      };
      header.onpointermove=e=>{
        if(!moving)return;
        const bound=n=>Math.max(-100000,Math.min(100000,Math.round(n)));
        const next={x:bound(moving.x+e.clientX-moving.startX),y:bound(moving.y+e.clientY-moving.startY)};
        points.set(id,next);place(next);edges();
      };
      header.onpointercancel=()=>{
        if(!moving)return;
        const original={x:moving.x,y:moving.y};
        moving=null;points.set(id,original);place(original);edges();
      };
      header.onpointerup=()=>{
        if(!moving)return;
        const next=points.get(id), changedPosition=next.x!==moving.x||next.y!==moving.y;
        moving=null;
        if(changedPosition&&setPosition(id,next.x,next.y))render();
      };
      const rows=h("div",{class:"wf-canvas-ports"});
      for(const [name,contract] of entries(ports[id]?.inputs)) {
        const target=h("div",{class:"wf-canvas-input",text:"In · "+name,...test("canvas-input-"+id+"-"+name)});
        const accepts=()=>connection&&!wouldCycle(id,connection.node)&&compatible(connection.contract,contract);
        target.ondragover=e=>{
          if(accepts()){e.preventDefault();e.dataTransfer.dropEffect="link";}
        };
        target.ondrop=e=>{
          if(!accepts())return;
          e.preventDefault();
          const {node,port}=connection;
          connection=null;
          if(setNodeBinding(id,name,{kind:"node",node,port,path:[]}))render();
        };
        rows.append(target);
      }
      for(const [name,contract] of entries(ports[id]?.outputs)) {
        const output=h("div",{class:"wf-canvas-output",draggable:"true",text:"Out · "+name,...test("canvas-output-"+id+"-"+name)});
        output.ondragstart=e=>{
          connection={node:id,port:name,contract};
          e.dataTransfer.setData("text/plain",id+"."+name);
          e.dataTransfer.effectAllowed="link";
        };
        output.ondragend=()=>{connection=null;};
        rows.append(output);
      }
      card.append(rows);
      surface.append(card);
    }
    edges();scroll.append(surface);region.append(scroll);
    return region;
  }
  function bindingForm(label, binding, set, skip) {
    const options = [["","Choose a source"], ...Object.keys(draft.inputs).map(port => [JSON.stringify({kind:"input",port}), "Input · "+port]),
      ...Object.keys(draft.constants).map(name => [JSON.stringify({kind:"constant",name}), "Constant · "+name])];
    for (const node of Object.keys(draft.nodes)) if (!wouldCycle(skip,node)) for (const port of Object.keys(ports[node]?.outputs || {}))
      options.push([JSON.stringify({kind:"node",node,port}), node+" · "+port]);
    const sourceKey = binding ? JSON.stringify(Object.fromEntries(entries(binding).filter(([k]) => k !== "path").sort(([a],[b]) => a.localeCompare(b)))) : "";
    const normalized = options.map(([key,title]) => [key ? JSON.stringify(Object.fromEntries(entries(JSON.parse(key)).sort(([a],[b]) => a.localeCompare(b)))) : "", title]);
    const area = h("div", {class:"wf-binding"});
    area.append(select(label, normalized, sourceKey, value => {
      set(value ? {...JSON.parse(value), path:[]} : undefined);changed();render();
    }, "binding-source"));
    if (binding) area.append(pathForm(binding.path || [], path => {binding.path = path;changed();}));
    return area;
  }
  function pathForm(path, update) {
    const wrap = h("details", {open:path.length > 0}, h("summary", {text:"Field or array path"}));
    const redraw = () => {
      while (wrap.children.length > 1) wrap.lastChild.remove();
      path.forEach((part,index) => {
        const kind = typeof part === "number" ? "index" : "key";
        wrap.append(h("div",{class:"row"}, select("Segment type",[["key","Field"],["index","Array index"]],kind,v=>{
          path[index] = v === "index" ? 0 : "";update(path);redraw();
        }),field("Segment "+(index+1),part,(value,control)=>{
          control.setCustomValidity("");
          if (kind === "key" || /^\d+$/.test(value) && Number(value) <= 2147483647) {
            path[index] = kind === "key" ? value : Number(value);update(path);
          } else {control.setCustomValidity("Use a whole array index from 0 to 2147483647.");changed();}
        }),button("Remove segment",()=>{path.splice(index,1);update(path);redraw();},"remove-segment")));
      });
      wrap.append(button("Add segment",()=>{path.push("");update(path);redraw();},"add-segment",path.length>=16));
    };
    redraw();return wrap;
  }
  function contractForm(row, name, side) {
    const editor = contracts.editor({test:"wf-contract", text:row.contract, onChange:(text, projected) => {
      row.contract = text; row.format = projected.format; changed();
    }});
    contractEditors.push(editor);
    return h("details", {}, h("summary", {text:"Contract · "+name}), editor.el, button("Copy saved contract", async () => {
      const picker = contracts.picker({test:"wf-contract-picker"});
      await dialog("Copy a saved contract", picker.el, ["Copy","Cancel"], async choice => {
        if (choice !== "Copy") return true;
        const pin = picker.pin();
        if (!pin?.digest) return false;
        const data = await api("/api/contracts/"+encodeURIComponent(pin.name)+"?revision="+pin.revision);
        if (data.digest !== pin.digest) throw new Error("The contract revision changed. Select it again.");
        row.contract = data.definition_text;changed();render();return true;
      });
    }, "copy-contract"));
  }
  function design() {
    const area = h("div", {...test("design")});
    if (!token) area.append(field("Workflow name",draft.name,v=>{draft.name=v;changed();}));
    area.append(field("Purpose",draft.purpose,v=>{draft.purpose=v;changed();},true));
    for (const side of ["inputs","outputs","constants"]) {
      const group = h("fieldset",{},h("legend",{text:side[0].toUpperCase()+side.slice(1)}));
      for (const [name,row] of entries(draft[side])) {
        const textMode = side === "constants" && row.value.startsWith('"');
        group.append(h("div",{class:"wf-item"}, h("strong",{text:name}),
          toggle("Local only",row.local_only,v=>{row.local_only=v;changed();}),
          side === "constants" ? h("div",{}, select("Constant format", [["text","Text"],["json","Exact JSON"]], row.value.startsWith('"') ? "text" : "json", v=>{
            row.value=v==="text"?'""':"null";changed();render();
          }),field("Constant value",row.value.startsWith('"') ? JSON.parse(row.value) : row.value,v=>{
            row.value=textMode?JSON.stringify(v):v;changed();
          },true)) : h("div",{},field("Description",row.description,v=>{row.description=v;changed();}),contractForm(row,name,side)),
          side === "outputs" ? bindingForm("Output source",row.binding,v=>{if(v)row.binding=v;else delete row.binding;}) : null,
          button("Remove "+name,()=>{delete draft[side][name];detach(b=>b&&(side==="inputs"&&b.kind==="input"&&b.port===name || side==="constants"&&b.kind==="constant"&&b.name===name));changed();render();},"remove-port")));
      }
      const singular = {inputs:"input",outputs:"output",constants:"constant"}[side];
      group.append(button("Add "+singular,async()=>{
        const name=await newKey("Add "+singular,draft[side]);if(!name)return;
        putKey(draft[side],name,side==="constants"?{value:'""',local_only:false}:{contract:'{"format":"text"}',format:"text",local_only:false});
        changed();render();
      },"add-"+singular));
      area.append(group);
    }
    const points=positions();
    area.append(canvas(points));
    const nodes=h("fieldset",{},h("legend",{text:"Nodes"}));
    for (const [id,node] of entries(draft.nodes)) {
      const src=sources[id], detail=ports[id];
      const group=h("fieldset",{},h("legend",{text:id+" · "+(src?.title || selections[id]?.key || "")}));
      const sourceLabel=h("p",{...test("source-"+id),text:(src?.revision_number ? "Captured revision "+src.revision_number : "Captured source")+
        (detail?.role ? " · Role "+detail.role : detail?.tool ? " · "+detail.server+"/"+detail.tool : "")});
      if(node.gate)gateForm(id,node,group);else group.append(sourceLabel);
      if (src?.key) {
        const marker=generation;
        wsApi("workflow-source-options/"+encodeURIComponent(src.key)+(src.revision?"?revision="+encodeURIComponent(src.revision):""))
          .then(result=>{if(marker===generation&&group.isConnected) {
            if (!src.revision && src.fingerprint && result.selection?.fingerprint !== src.fingerprint) sourceLabel.textContent="Workflow snapshot";
            else group.append(button("Open source",()=>openBlock(src.key,src.revision),"open-source"));
          }}).catch(()=>{if(marker===generation&&group.isConnected)sourceLabel.textContent="Workflow snapshot · saved source unavailable";});
      }
      for(const [name,port] of entries(detail?.inputs)) group.append(bindingForm(
        "Bind "+name+" · "+port.format+(port.local_only?" · Local only":""),node.bindings[name],v=>setNodeBinding(id,name,v),id));
      for(const [name,port] of entries(detail?.outputs)) group.append(h("p",{text:"Output "+name+" · "+port.format+(port.local_only?" · Local only":"")}));
      group.append(button("Rebind "+id,()=>pickSource(id),"rebind-node",!node.snapshot_id || !!rebind || Object.keys(selections).length>0),
        button("Remove "+id,()=>{delete draft.nodes[id];delete sources[id];delete ports[id];delete selections[id];delete layout[id];if(rebind===id)rebind=null;detach(b=>b?.kind==="node"&&b.node===id);changed();render();},"remove-node"));
      group.append(layoutForm(id,points.get(id)));
      nodes.append(group);
    }
    nodes.append(button("Add node",()=>pickSource(),"add-node",!!rebind),
      button("Add Release Gate",addGate,"add-gate",!!rebind));
    area.append(nodes);
    const budget=h("details",{},h("summary",{text:"Limits and inspection"}));
    for(const [key,label,fallback] of [["max_seconds","Seconds",1800],["max_model_attempts","Model attempts",32]])
      budget.append(field(label,draft.budget?.[key]??fallback,v=>{draft.budget??={};draft.budget[key]=/^\d+$/.test(v)?Number(v):null;changed();}));
    budget.append(button("Inspect exact definition",async()=>{
      const result=await wsApi("workflow-definition",{editor:draft});
      await dialog("Exact workflow definition",h("pre",{text:result.definition_text}),["Close"]);
    },"inspect-definition"));
    area.append(budget);
    return area;
  }
  async function pickSource(replacement) {
    const blocks=(await api("/api/blocks")).blocks;
    const box=h("div"), picker=h("select", {...test("block-picker")}), revisions=h("select",{...test("revision-picker")}), msg=h("p",{role:"status"});
    let details, epoch=0;
    function filter(query) {
      picker.replaceChildren(new Option("Choose a block",""),...blocks.filter(block=>(block.display_name+" "+block.name).toLowerCase().includes(query.toLowerCase()))
        .map(block=>new Option((block.display_name||block.name)+" · "+block.kind,block.name)));
      details=null; revisions.replaceChildren(); epoch++;
    }
    box.append(field("Search blocks","",filter),h("label",{class:"ws-field"},"Block",picker),h("label",{class:"ws-field"},"Exact revision",revisions),msg);
    filter("");
    async function inspect(loadRevisions) {
      const marker=++epoch, key=picker.value;
      details=null;
      if(!key)return;
      const base="workflow-source-options/"+encodeURIComponent(key);
      try{
        let data=await wsApi(base+(!loadRevisions&&revisions.value?"?revision="+encodeURIComponent(revisions.value):""));
        if(marker!==epoch)return;
        if(loadRevisions){
          revisions.replaceChildren(...(data.revisions||[]).map(row=>new Option("Revision "+row.number+(row.title?" · "+row.title:""),row.id)));
          if(data.revisions?.length)data=await wsApi(base+"?revision="+encodeURIComponent(revisions.value));
        }
        if(marker!==epoch)return;
        details=data;
        msg.textContent=data.block.kind==="script"?"Host Python blocks cannot be added to workflows.":
          data.version!==4?"Upgrade this revision to v4 in Blocks before adding it.":"Ready to capture this revision.";
      }catch(e){if(marker===epoch)msg.textContent=e.message;}
    }
    picker.onchange=()=>inspect(true); revisions.onchange=()=>inspect(false);
    let id=replacement||"";
    if(!replacement)box.append(field("Node identifier","",v=>id=v));
    const action=await dialog(replacement?"Choose replacement revision":"Add workflow node",box,[replacement?"Use revision":"Add","Cancel"],choice=>{
      if(choice==="Cancel")return true;
      if(details?.version===4&&details.block.kind!=="script"&&/^[A-Za-z_][A-Za-z0-9_]*$/.test(id)&&
          (replacement||!Object.hasOwn(draft.nodes,id)))return true;
      msg.textContent="Choose an eligible revision and a unique node identifier.";return false;
    });
    if(!action||action==="Cancel")return;
    if(replacement){
      rebind=id;
      for(const name of Object.keys(draft.nodes[id].bindings)) if(!Object.hasOwn(details.block.inputs,name))delete draft.nodes[id].bindings[name];
      detach(b=>b?.kind==="node"&&b.node===id&&!Object.hasOwn(details.block.outputs,b.port));
    } else putKey(draft.nodes,id,{bindings:{}});
    putKey(selections,id,details.selection);putKey(ports,id,details.block);
    putKey(sources,id,{key:details.selection.key,title:details.selection.key,revision:details.selection.revision});
    changed();render();
  }
  function editExample(existing) {
    const example = existing ? structuredClone(existing) : {id:crypto.randomUUID(),version:1,label:"",inputs:{...inputs},
      expectation:{kind:"review",criterion:""},verified:false,held_out:false};
    const box=h("div",{}, field("Example label",example.label,v=>example.label=v));
    for(const [name,port] of entries(draft.inputs))box.append(field(name+(port.format==="json"?" (exact JSON)":""),example.inputs[name],
      v=>putKey(example.inputs,name,v),true));
    for (const name of Object.keys(example.inputs)) if (!Object.hasOwn(draft.inputs,name)) delete example.inputs[name];
    const expectation=h("div");
    function drawExpectation() {
      expectation.replaceChildren();
      const e=example.expectation;
      if(e.kind==="review")expectation.append(field("Review criterion",e.criterion,v=>e.criterion=v,true));
      if(e.kind==="whole_output")for(const [name,port] of entries(draft.outputs))expectation.append(field(
        "Expected "+name+(port.format==="json"?" (exact JSON)":""),e.values[name],v=>putKey(e.values,name,v),true));
      if(e.kind==="fields"){
        e.assertions.forEach((row,index)=>expectation.append(h("fieldset",{},h("legend",{text:"Check "+(index+1)}),
          select("Output",[["","Choose JSON output"],...entries(draft.outputs).filter(([,port])=>port.format==="json").map(([name])=>[name,name])],row.output,v=>row.output=v),
          pathForm(row.path,v=>row.path=v),
          select("Check",[["equals","Equals"],["present","Present"],["absent","Absent"],["null","Is null"]],row.operation,v=>{
            row.operation=v;if(v!=="equals")delete row.value_text;drawExpectation();
          }),row.operation==="equals"?field("Expected exact JSON",row.value_text,v=>row.value_text=v,true):null,
          button("Remove check",()=>{e.assertions.splice(index,1);drawExpectation();},"remove-check"))));
        expectation.append(button("Add check",()=>{e.assertions.push({output:"",path:[],operation:"present"});drawExpectation();},"add-check"));
      }
    }
    box.append(select("Expectation",[["review","Human review"],["whole_output","Whole outputs"],["fields","Field checks"]],example.expectation.kind,v=>{
      example.expectation=v==="review"?{kind:v,criterion:""}:v==="fields"?{kind:v,assertions:[]}:{kind:v,values:{}};
      drawExpectation();
    }),expectation,toggle("Verified expectation",example.verified,v=>example.verified=v),toggle("Held out",example.held_out,v=>example.held_out=v));
    drawExpectation();
    return dialog("Workflow example",box,["Keep example","Cancel"]).then(choice=>{
      if(choice!=="Keep example")return;
      for(const name of Object.keys(draft.inputs)) if(!Object.hasOwn(example.inputs,name))putKey(example.inputs,name,"");
      if(example.expectation.kind==="whole_output") {
        for(const name of Object.keys(draft.outputs)) if(!Object.hasOwn(example.expectation.values,name))putKey(example.expectation.values,name,"");
        for(const name of Object.keys(example.expectation.values)) if(!Object.hasOwn(draft.outputs,name))delete example.expectation.values[name];
      }
      if(existing){
        if(JSON.stringify(existing.inputs)!==JSON.stringify(example.inputs)||JSON.stringify(existing.expectation)!==JSON.stringify(example.expectation)){
          example.version++;example.verified=false;
        }
        examples=examples.map(row=>row.id===existing.id?example:row);
      }else examples.push(example);
      changed();render();
    });
  }
  function exampleView() {
    return h("div",{},h("p",{text:"Save the workflow to keep example edits. Saved inputs can contain protected values in unencrypted local files."}),
      examples.map(example=>h("div",{class:"wf-item"},
        h("strong",{text:example.label||example.id}),
        h("p",{text:"Version "+example.version+" · "+(example.verified?"Verified":"Unverified")+(example.held_out?" · Held out":"")}),
        button("Edit example",()=>editExample(example),"edit-example"),
        button("Remove example",()=>{examples=examples.filter(row=>row.id!==example.id);selected.delete(example.id);changed();render();},"remove-example"))),
      button("Add example",()=>editExample(),"add-example",examples.length>=20));
  }
  function runRequest() {
    if(selected.size)return {name:draft.name,examples:examples.filter(row=>selected.has(row.id)).map(({id,version})=>({id,version}))};
    return {name:draft.name,inputs:Object.fromEntries(Object.keys(draft.inputs).map(key=>[key,inputs[key]??""]))};
  }
  function destinations(plan) {
    return [...new Map([plan,...(plan.example_plans||[])].flatMap(p=>entries(p.destinations))
      .map(([node,d])=>[JSON.stringify([node,d.kind,d.url,d.model,d.tool,d.protected]),[node,d]])).values()];
  }
  function reviewView() {
    const d=plan.descriptor;
    return h("div",{},h("h3",{text:"Preflight"}),
      h("p",{text:"Order: "+d.order.join(" → ")}),
      h("p",{text:d.repetitions+" planned · "+examples.length+" saved examples · MCP confirmation: "+
        (d.mcp_confirmation.mode==="every_run"?"Every MCP workflow run":"Only MCP batches")}),
      d.mcp_confirmation.invalid?h("p",{text:"The saved preference is invalid. Every MCP workflow run requires confirmation."}):null,
      h("p",{text:d.saving?"Run evidence can store protected values in unencrypted local files.":"Run evidence stays in this session."}),
      issues(d.issues), d.issues.some(row=>row.code==="tool_unverified")?
        [...new Set(destinations(d).filter(([,item])=>item.kind==="mcp"&&!item.observation).map(([,item])=>item.server))].map(server=>
          button("Refresh tools · "+server,()=>exclusive(async()=>{
            const config=await api("/api/state");
            await api("/api/mcp/tools",{server,revision:config.mcp.revision});
            await preflight();
          }),"refresh-tools")):null,
      d.restricted_sources?.map(row=>h("p",{text:row.source.join(" · ")+" is local only · "+row.origin})),
      destinations(d).map(([node,x])=>h("p",{text:node+" · "+x.kind+" · "+x.url+" · "+(x.model||x.tool||"")+
        (x.kind==="mcp"?" · Effects "+x.effects+" · Claim "+x.effect_claim:"")+(x.protected?" · Protected":"")})),
      button("Workflow settings",()=>settings(destinations(d).map(([,item])=>item)),"preflight-settings"),
      destinations(d).some(([,item])=>item.kind==="mcp")?button("MCP servers",openServers,"open-mcp"):null);
  }
  async function preflight() {
    if(dirty||!token)return;
    invalidatePlan();
    planRequest=runRequest();
    plan=await planFor("run",planRequest);
    render();
    main.querySelector('[data-test="wf-review"]')?.focus();
  }
  async function start() {
    if(!pendingStart){
      if(!plan||dirty||job?.running||plan.descriptor.issues.some(row=>row.severity!=="warning"))return;
      if(plan.required_confirmations.length&&await dialog("Confirm MCP workflow execution",reviewView(),["Confirm","Cancel"])!=="Confirm")return;
      const ticket=(await wsApi("tickets",{})).ticket;
      pendingStart={...reviewBody(planRequest,plan),ticket};
    }
    const body=pendingStart;
    let result;
    try {
      result=await wsApi(route(body.request.name,"/run"),body);
    } catch(e) {
      result=await wsApi("tickets/"+body.ticket).catch(()=>null);
      if(!result?.operation_id){
        if(e.code==="ticket_expired"){
          pendingStart=null;
          invalidatePlan();
          report("The start ticket expired. Check run history before starting again.");
        }else if(result?.state==="unused"&&e.code&&e.status<500){
          pendingStart=null;
          invalidatePlan();
          error(e);
        }else{
          report("Run start could not be confirmed. Retry start with the same ticket.");
        }
        render();
        return;
      }
    }
    pendingStart=null;
    if(result.start_failed){
      invalidatePlan();
      render();
      report("Run start was rejected. Run Preflight again.");
      return;
    }
    job={id:result.operation_id,name:body.request.name,running:true,batch:!!body.request.examples};
    report("");
    render();
    poll(job,generation);
  }
  async function poll(current, epoch) {
    try {
      const next=await wsApi("workflow-jobs/"+encodeURIComponent(current.id));
      if(epoch!==generation||current!==job)return;
      current.result=next;
      current.running=next.outcome==="running";
      if(current.running)renderTrace();else render();
      if(current.running)timer=setTimeout(()=>poll(current,epoch),400);
    }catch(e){if(epoch===generation&&current===job){error(e);timer=setTimeout(()=>poll(current,epoch),1800);}}
  }
  function outputRows(outputs) {
    return entries(outputs).map(([key,row])=>h("div",{class:"wf-item"},h("strong",{text:key+(row.local_only?" · Local only":"")}),
      h("pre",{text:row.text}),copyControl("wf-copy-output",()=>row.text,null)));
  }
  function showTrace(trace) {
    const content=h("div",{},trace.outcome?h("p",{text:"Outcome: "+trace.outcome}):null,issues(trace.issues),outputRows(trace.outputs));
    if(trace.release_review) {
      const review=trace.release_review;
      content.append(h("h3",{text:"Release review"}),h("pre",{text:review.payload,...test("review-payload")}),
        copyControl("wf-copy-review",()=>review.payload,null),
        h("p",{text:"Decision: "+trace.release_decision.decision+" · "+trace.release_decision.timestamp}),
        h("p",{text:"Identifier check: "+review.identifier_check.status+" · "+review.identifier_check.hits.length+" matches"}),
        h("p",{text:"Local AI assessment: "+review.assessment.status}),
        review.assessment.findings.map(item=>h("p",{text:item.concern+" · "+item.location+" · "+item.explanation})));
      if(trace.assessment_trace)content.append(h("details",{},h("summary",{text:"Local assessment trace"}),showTrace(trace.assessment_trace)));
    }
    for(const [key,value] of entries(trace.inputs)) {
      const text=value?.text;
      if(typeof text==="string")content.append(h("details",{},h("summary",{text:"Input · "+key}),h("pre",{text})));
    }
    for(const step of trace.steps || []) {
      content.append(h("div", {}, h("h4",{text:step.kind+" · "+step.outcome}),
        step.failure?h("p",{text:step.failure}):null,
        step.repairs_used!==undefined?h("p",{text:"Repairs used: "+step.repairs_used}):null,
        entries(step.output_checks).map(([key,row])=>h("p",{text:key+" · "+(row.overall || "check recorded")}))));
      for(const id of step.attempts || [])content.append(button("Inspect attempt "+id,async()=>{
        const item=await api("/api/attempts/"+encodeURIComponent(id));
        await dialog("Model attempt",showTrace(item.attempt || item.record || item),["Close"]);
      },"inspect-attempt"));
    }
    for(const [key,value] of entries(trace))if(!["outcome","issues","outputs"].includes(key))
      content.append(h("details",{},h("summary",{text:key.replaceAll("_"," ")}),h("pre",{text:JSON.stringify(value,null,2)})));
    return content;
  }
  async function inspectNode(key) {
    const current=job, epoch=generation;
    const trace=await wsApi("workflow-jobs/"+encodeURIComponent(current.id)+"?node="+encodeURIComponent(key));
    if(epoch===generation&&current===job)await dialog("Node trace",showTrace(trace),["Close"]);
  }
  function nodeRows(nodes,index) {
    return entries(nodes).map(([id,row])=>h("p",{text:id+" · "+row.outcome+(row.reason?" · "+row.reason:"")+
      (row.elapsed_ms!==undefined?" · "+row.elapsed_ms+" ms":"")+" "},
      row.detail_available?button("Inspect "+id,()=>inspectNode(index===undefined?id:index+":"+id),"inspect-node"):
        row.outcome!=="not_run"&&row.outcome!=="dispatch_pending"?h("span",{text:"Trace omitted from session preview."}):null));
  }
  function renderTrace() {
    const area=main.querySelector('[data-test="wf-trace"]');
    if(!area||!job)return;
    const result=job.result||{outcome:"running"};
    replace(area,h("h3",{text:job.name+" · "+result.outcome}), h("p",{text:"Run "+job.id}),
      job.running?button("Stop",()=>wsApi("workflow-jobs/"+encodeURIComponent(job.id)+"/stop",{}),"stop"):null,
      job.running?h("p",{text:"This run occupies the shared Workshop slot. Test, Compare and assistance become available when it finishes."}):null,
      result.release?releasePanel(result.release):result.phase==="assessing_release"?h("p",{text:"Assessing release locally…"}):null,
      issues(result.issues),result.save_error?h("p",{text:result.save_error}):null,nodeRows(result.nodes,job.batch?result.example_index:undefined),outputRows(result.outputs),
      result.metrics?h("p",{text:`Executed ${result.metrics.executed}/${result.metrics.selected} · Complete ${result.metrics.complete}/${result.metrics.selected} · Checks passed ${result.metrics.checks_passed}/${result.metrics.selected} · Correct ${result.metrics.correct} · Incorrect ${result.metrics.incorrect} · Unknown ${result.metrics.unknown}`}):null,
      result.examples?.map((row,index)=>h("details",{},h("summary",{text:(examples.find(e=>e.id===row.id)?.label||row.id)+" · "+row.outcome+" · "+row.correctness}),
        row.reason?h("p",{text:row.reason}):null,
        issues(row.issues),nodeRows(row.nodes,index),outputRows(row.outputs),
        result.reviews?.filter(review=>review.example===row.id).map(review=>h("p",{text:"Human review: "+review.judgement+(review.note?" · "+review.note:"")})),
        row.outputs_omitted?h("p",{text:"Outputs exceed the session preview limit."}):null,
        row.outcome==="complete"?button("Review correctness",()=>reviewExample(row,result),"human-review"):null)));
    if(!job.running&&plan&&!dirty&&!plan.descriptor.issues.some(row=>row.severity!=="warning"))main.querySelector('[data-test="wf-run"]')?.removeAttribute("disabled");
  }
  async function reviewExample(row,result) {
    let note="";
    const choice=await dialog("Review workflow result",field("Note","",v=>note=v,true),["Correct","Incorrect","Unknown","Cancel"]);
    if(!choice||choice==="Cancel")return;
    await wsApi("workflow-jobs/"+encodeURIComponent(job.id)+"/review",{example:row.id,version:row.version,
      executable_digest:result.executable_digest,result_digest:row.result_digest,judgement:choice.toLowerCase(),note});
    job.result=await wsApi("workflow-jobs/"+encodeURIComponent(job.id));renderTrace();
    report("Human review recorded for this result.");
  }
  function runView() {
    const area=h("div",{},dirty?h("p",{text:"Save changes before running this workflow."}):null);
    const options=h("fieldset",{},h("legend",{text:"Run inputs"}));
    for(const [name,port] of entries(draft.inputs))options.append(field(name+(port.format==="json"?" (exact JSON)":""),inputs[name],v=>{putKey(inputs,name,v);invalidatePlan();},true));
    if(examples.length)options.append(h("p",{text:"Select saved examples to run a batch. With no selections, Run uses the inputs above."}),
      ...examples.map(row=>h("div",{},toggle((row.label||row.id)+(row.held_out?" · Held out":""),selected.has(row.id),checked=>{
        if(checked)selected.add(row.id);else selected.delete(row.id);invalidatePlan();
      }))));
    options.disabled=!!(job?.running||pendingStart);
    area.append(options,button("Preflight",()=>exclusive(preflight),"preflight",dirty||!token||options.disabled),
      button(pendingStart?"Retry start":"Run",()=>exclusive(start),"run",!pendingStart&&
        (!plan||plan.descriptor.issues.some(row=>row.severity!=="warning")||!!job?.running)),
      h("section",{...test("review"),tabindex:"-1"},plan?reviewView():null),h("section",{...test("trace")}));
    return area;
  }
  function render() {
    contractEditors=[];
    const active=!!(job?.running||pendingStart);
    const toolbar=h("div",{class:"row"},select("Saved workflow",[["","Choose a workflow"],...names.map(name=>[name,name])],token?draft?.name:"",open,"picker"),
      button("New",()=>open("",true),"new",active),button("Save",()=>exclusive(save),"save",!draft||!dirty||active),
      button("Delete",()=>exclusive(removeWorkflow),"delete",!token||dirty||active));
    toolbar.querySelector("select").disabled=active;
    main.replaceChildren(toolbar);
    if(!draft)return;
    main.append(h("h3",{...test("identity"),text:(draft.name||"New workflow")+(dirty?" · Unsaved":"")}),
      h("div",{class:"row"},[["design","Design"],["run-view","Run"],["examples","Examples"]].map(([id,label])=>{
        const b=button(label,()=>{view=id;render();},id,active&&id!=="run-view");
        b.setAttribute("aria-pressed",String(view===id));return b;
      })),view==="design"?design():view==="examples"?exampleView():runView());
    renderTrace();
  }
  async function settings(candidates=[]) {
    const content=h("div"), msg=h("p",{role:"status"}), body=h("div");
    content.append(body,msg);
    const pref=await wsApi("workflow-mcp-confirmation");
    let mode=pref.mode;
    body.append(select("Workflow MCP confirmation",[["every_run","Every MCP workflow run"],["effectful_batches","Only MCP batches"]],mode,v=>mode=v,"mcp-confirmation"),
      pref.invalid?h("p",{text:"Invalid saved setting. Using Every MCP workflow run."}):null,
      button("Save confirmation preference",async()=>{
        const result=await wsApi("workflow-mcp-confirmation",{mode,expected_revision:pref.revision});
        Object.assign(pref,result);invalidatePlan();msg.textContent="Confirmation preference saved.";
      },"save-mcp-confirmation"));
    const grants=h("section");
    body.append(grants);
    function exactDestination(item) {
      const {kind,url}=item;
      return kind==="mcp"?{kind,url,server:item.server,tool:item.tool}:{kind,url,model:item.model,provider:item.provider||""};
    }
    async function reviewTrust(destination, trusted) {
      const preview=await wsApi("destination-trust-preview",{destination});
      if(trusted&&preview.reason!=="destination_untrusted"&&preview.reason!==null){
        msg.textContent=preview.reason==="destination_proxied"?"This endpoint is proxied. Configure a loopback proxy bypass first.":
          "Use a literal loopback IPv4 or IPv6 address. Hostnames and remote endpoints are ineligible.";
        return;
      }
      if(await dialog(trusted?"Grant destination trust":"Revoke destination trust",h("p",{text:label(preview.destination||destination)}),["Confirm","Cancel"])!=="Confirm")return;
      await wsApi("destination-trust",{destination:preview.destination,expected_revision:preview.revision,trusted});
      invalidatePlan();
      await loadGrants();
      msg.textContent=trusted?"Destination trusted.":"Destination trust revoked.";
    }
    const label = (item) => [item.kind,item.url,item.server,item.tool,item.model,item.provider].filter(Boolean).join(" · ");
    async function loadGrants() {
      const data=await wsApi("destination-trust");
      replace(grants,h("h3",{text:"Destination trust"}),h("p",{text:"Trust applies only to this workspace and exact destination."}),
        data.grants.map(item=>h("p",{},label(item)," ",button("Revoke",()=>reviewTrust(item,false),"revoke-trust"))),
        candidates.map(exactDestination).map(item=>h("p",{},label(item)," ",button("Review trust",()=>reviewTrust(item,true),"candidate-trust"))));
    }
    await loadGrants();
    const fields=h("fieldset",{},h("legend",{text:"Review a destination"}));
    const destination={kind:"lmstudio",url:"",model:"",provider:"",server:"",tool:""};
    const names=h("div");
    function drawFields(){
      replace(names,(destination.kind==="mcp"?["server","tool"]:["model","provider"]).map(key=>field(key,destination[key],v=>destination[key]=v)));
    }
    fields.append(select("Kind",[["lmstudio","LM Studio"],["openai_compat","OpenAI compatible"],["mcp","MCP tool"]],destination.kind,v=>{
      destination.kind=v;drawFields();
    }),field("Endpoint URL","",v=>destination.url=v),names,button("Review destination",()=>reviewTrust(exactDestination(destination),true),"review-trust"));
    drawFields();body.append(fields);
    await dialog("Workflow settings",content,["Close"]);
  }
  async function evidence() {
    const area=h("div"), box=h("div",{},area);
    async function listing() {
      const {jobs}=await wsApi("workflow-evidence");
      replace(area,jobs.length?jobs.map(id=>button("Open "+id,async()=>{
        const root="workflow-evidence/"+encodeURIComponent(id);
        const manifest=await wsApi(root);
        const records=h("div",{},h("p",{text:manifest.name+" · "+manifest.outcome}),
          manifest.intent?h("p",{text:"Unsettled dispatch · "+manifest.intent.node}):null);
        for(const name of manifest.records)records.append(button(name,async()=>{
          const trace=await wsApi(root+"?record="+encodeURIComponent(name));
          await dialog("Saved workflow record",showTrace(trace),["Close"]);
        },"evidence-record"));
        if(await dialog("Saved workflow run",records,["Delete evidence","Close"])==="Delete evidence"&&
          await dialog("Delete saved evidence",h("p",{text:"Permanently delete this saved workflow run?"}),["Delete","Cancel"])==="Delete"){
          await wsApi(root+"/delete",{});await listing();
        }
      },"evidence-job")):h("p",{text:"No saved workflow runs."}));
    }
    await listing();
    await dialog("Workflow history",box,["Close"]);
  }
  const putKey = (obj,key,value) => Object.defineProperty(obj,key,{value,enumerable:true,writable:true,configurable:true});
  async function recoverActive() {
    if(job||dirty)return;
    const active=await wsApi("workflow-active");
    if(!active.operation_id)return;
    try {load(await wsApi(route(active.name)));}
    catch {load(null);draft.name=active.name;dirty=false;}
    view="run-view";job={id:active.operation_id,name:active.name,running:true,batch:active.batch};
    poll(job,generation);
  }
  render();
  return {onShow:()=>exclusive(async()=>{await refresh();await recoverActive();render();}).catch(error), leave, settings, evidence,
    tutorial:async(name,text,example)=>{
      await refresh();load(await wsApi(route(name)));view="run-view";inputs.text=text;
      if(example)selected.add(example);render();
    }
  };
};

