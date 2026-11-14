/* The form and runner use the same reviewed Workshop operations. Exact values stay as text. */
"use strict";
window.createWorkflowPage = function ({ h, api, wsApi, dialog, copyControl, contracts, openBlock, openServers, newBlock, activity }) {
  const panel = document.getElementById("panel-workflows");
  const status = h("p", {role:"status", "data-test":"wf-status"});
  const main = h("div");
  panel.append(h("h2", {text:"Workflows"}), status, main);
  let draft, token, dirty = false, names = [], ports = {}, sources = {}, selections = {}, layout = {};
  let examples = [], selected = new Set(), inputs = {}, view = "design", plan, planRequest;
  let busy = false, job, pendingStart, timer, generation = 0, rebind = null, contractEditors = [];
  let releaseAcknowledgements={}, releaseSubmitting=false, releasePanelToken=null, releasePanelElement=null;
  // Workflow Design (UI-CD v3 CD1): selection, viewport, undo, the validation client and port projections held
  // per capture. `vgen` never resets within the page session; `vcontext` changes on every load.
  let selection=null, listView=null, viewport={x:24,y:24,zoom:1}, undoStack=[], redoStack=[], heldPorts={};
  let vgen=0, vrequested=-1, vtimer=null, vaccepted=null, vstatus={state:"unchecked"}, vbasis="", vcontext=0;
  let connecting=null, suppressClick=false, canvasState=null, issuesOpen=false, paletteOpen=false, constantsOpen=false, band="wide";
  const palette={blocks:null,details:new Map(),query:"",highlight:null};
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
    bumpValidation();
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
  // `keep` preserves run inputs, selected examples, the canvas selection, the viewport and the view across a Save
  // or a return from a detour (CD1.13); undo history never survives a load (CD1.7).
  function load(data, keep = null) {
    clearRun();
    cancelConnect(false);
    draft = data?.editor || {name:"", purpose:"", inputs:{}, outputs:{}, constants:{}, nodes:{}};
    token = data?.file_token ?? null;
    sources = data?.sources || {};
    ports = {}; heldPorts = {};
    selections = {};
    for (const [node,value] of entries(data?.node_ports)) setPorts(node, value);
    layout = data?.layout || {};
    examples = data?.examples || [];
    if (keep) {
      inputs = keep.inputs || {};
      selected = new Set([...(keep.selected || [])].filter(id => examples.some(row => row.id === id)));
      selection = keep.selection && selectionExists(keep.selection) ? keep.selection : null;
      if (keep.viewport) viewport = {...keep.viewport};
    } else {
      selected = new Set(); inputs = {}; selection = null; viewport = {x:24,y:24,zoom:1};
    }
    rebind = null;
    dirty = !data;
    view = keep?.view || "design";
    clearHistory();
    vcontext++; vaccepted = null; vstatus = {state:"unchecked"}; vrequested = -1;
    if (!examples.some(row => row.id === vbasis)) vbasis = "";
    vgen++;
    render();
    if (data?.issues?.length) main.querySelector('[data-test="wf-design-area"]')?.prepend(issues(data.issues));
  }
  async function reloadSaved() {
    load(await wsApi(route(draft.name)), {inputs, selected:[...selected], selection, viewport, view});
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
      if (!editor.el?.isConnected) continue;      // editors of a replaced inspector no longer hold the draft
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
    // A successful Save is an undo checkpoint (CD1.7); run inputs, examples, selection and viewport stay (CD1.13).
    load(await wsApi(route(body.name)), {inputs, selected:[...selected], selection, viewport, view});
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
    const port=()=>({format,description:"",local_only:false,value_types:null,contract:config.contract,identifier_map:false});
    return {kind:"gate",context_local_only:false,inputs:{value:port(),scan:{format:"json",description:"",local_only:false,value_types:null,contract:null,identifier_map:false}},
      outputs:{released:port()}};
  }
  // Adds a Gate with the existing defaults and a prefilled unique identifier (CD1.2).
  function addGate(point) {
    if(rebind)return;
    const gate={kind:"release",title:"Review redacted text",contract:'{"format":"text"}',
      max_release_wait_seconds:1800,assessment:{enabled:false}};
    edit(()=>{
      const id=uniqueId("gate",draft.nodes);
      putKey(draft.nodes,id,{gate,bindings:{}});putKey(ports,id,gatePorts(gate));
      putKey(layout,id,point||freeSlot());
      selection={kind:"node",id};
    });
  }
  function gateForm(id,node,group) {
    const gate=node.gate;
    group.append(field("Gate title",gate.title,v=>{gate.title=v;changed();}),
      field("Review wait seconds",gate.max_release_wait_seconds??1800,v=>{
        gate.max_release_wait_seconds=/^\d+$/.test(v)?Number(v):null;changed();}));
    const gateSummary=h("summary",{text:"Released value contract","data-test":"wf-gate-contract-chip"});
    const editor=contracts.editor({test:"wf-gate-contract",text:gate.contract,shortcuts:true,onView:view=>{
      const sum=contracts.summarize(view);gateSummary.textContent="Released value contract · "+sum.chip;gateSummary.title=sum.readable;},onChange:text=>{
      gate.contract=text;putKey(ports,id,gatePorts(gate));changed();}});
    contractEditors.push(editor);
    group.append(h("details",{},gateSummary,editor.el));
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
  // --- Release review (UI-CD v3 CD3): highlighting, finding navigation, Find, pagination, batch position,
  // destination facts, decision consequences and Reject and revise. The payload text and Copy are never changed.
  const PAYLOAD_PAGE=100000, PAYLOAD_PAGED=200*1024;
  function unitOffsets(text) {
    // UTF-16 index of each code point, plus the end: server hit offsets count code points.
    const out=[];let i=0;
    for(const ch of text){out.push(i);i+=ch.length;}
    out.push(i);return out;
  }
  function jsonSpans(text) {
    const spans=new Map();let i=0;
    const ws=()=>{while(i<text.length&&" \t\n\r".includes(text[i]))i++;};
    const str=()=>{const start=i;i++;while(i<text.length&&text[i]!=='"'){if(text[i]==="\\")i++;i++;}i++;return [start,i];};
    const value=(path,depth)=>{
      if(depth>256)throw new Error("depth");
      ws();const c=text[i];
      if(c==='"'){spans.set(JSON.stringify(path)+"|value",str());return;}
      if(c==="{"){i++;ws();if(text[i]==="}"){i++;return;}
        for(;;){ws();const span=str();const key=JSON.parse(text.slice(span[0],span[1]));spans.set(JSON.stringify([...path,key])+"|key",span);
          ws();i++;value([...path,key],depth+1);ws();if(text[i]===","){i++;continue;}i++;return;}}
      if(c==="["){i++;ws();if(text[i]==="]"){i++;return;}
        for(let n=0;;n++){value([...path,n],depth+1);ws();if(text[i]===","){i++;continue;}i++;return;}}
      while(i<text.length&&!",]} \t\n\r".includes(text[i]))i++;
    };
    try{value([],0);}catch{/* an unexpected shape just leaves fewer spans */}
    return spans;
  }
  function releaseLocations(review) {
    const text=review.payload, rows=[];
    if(review.format==="json"){
      const spans=jsonSpans(text);
      review.identifier_check.hits.forEach((hit,index)=>{const span=spans.get(JSON.stringify(hit.path)+"|"+hit.part);
        if(span)rows.push({kind:"hit",index,start:span[0],end:span[1]});});
    } else {
      const units=unitOffsets(text);
      review.identifier_check.hits.forEach((hit,index)=>{if(hit.start<units.length&&hit.end<units.length)
        rows.push({kind:"hit",index,start:units[hit.start],end:units[hit.end]});});
    }
    const lower=text.toLowerCase();
    review.assessment.findings.forEach((item,index)=>{
      const at=item.location?lower.indexOf(item.location.toLowerCase()):-1;
      if(at>=0)rows.push({kind:"finding",index,start:at,end:at+item.location.length});
    });
    return rows.sort((a,b)=>a.start-b.start||a.end-b.end);
  }
  function releasePanel(review) {
    if(releasePanelToken===review.token&&releasePanelElement)return releasePanelElement;
    const total=job?.total||(job?.result?.examples?.length)||0, batch=!!job?.batch;
    const exampleId=job?.result?.examples?.[review.example_index]?.id||planRequest?.examples?.[review.example_index]?.id;
    const label=examples.find(row=>row.id===exampleId)?.label||exampleId||"";
    const box=h("section",{...test("release-panel"),"data-gate":review.gate,"data-example":String(review.example_index??"")},
      h("h3",{text:"Release Gate · "+review.title}),
      batch?h("p",{...test("release-batch"),"data-index":String((review.example_index??0)+1),"data-total":String(total),
        text:"Example "+((review.example_index??0)+1)+(total?" of "+total:"")+(label?" · "+label:"")+". Each example needs its own decision; rejecting ends the batch."}):null,
      h("p",{text:"Review the complete value and its destinations. Release permits downstream processing; this is not an anonymity certificate or a complete HTTP request preview."}));
    // Payload with highlighted locations, pagination above 200 KB, Find and Previous/Next finding.
    const text=review.payload, locations=releaseLocations(review);
    const pages=[];
    if(text.length>PAYLOAD_PAGED){
      for(let at=0;at<text.length;){let end=Math.min(text.length,at+PAYLOAD_PAGE);
        if(end<text.length&&/[\uD800-\uDBFF]/.test(text[end-1]))end--;pages.push([at,end]);at=end;}
    } else pages.push([0,text.length]);
    let page=0, active=null, found=[], findIndex=-1;
    const pre=h("pre",{class:"wf-release-payload",...test("release-payload"),"data-pages":String(pages.length)});
    const pageLabel=h("span",{...test("release-page"),"data-page":"1"});
    function draw() {
      const [from,to]=pages[page];
      const marks=[...locations.map((row,i)=>({...row,id:"l"+i})),...found.map((at,i)=>({kind:"find",start:at[0],end:at[1],id:"f"+i}))]
        .filter(row=>row.end>from&&row.start<to).sort((a,b)=>a.start-b.start);
      const parts=[];let cursor=from;
      for(const row of marks){
        const start=Math.max(row.start,cursor,from), end=Math.min(row.end,to);
        if(end<=start)continue;
        if(start>cursor)parts.push(document.createTextNode(text.slice(cursor,start)));
        const isActive=active&&active.id===row.id;
        parts.push(h("mark",{class:"wf-mark","data-kind":row.kind,"data-mark":row.id,...(isActive?{"data-active":"true"}:{}),text:text.slice(start,end)}));
        cursor=end;
      }
      if(cursor<to)parts.push(document.createTextNode(text.slice(cursor,to)));
      pre.replaceChildren(...parts);
      pageLabel.dataset.page=String(page+1);pageLabel.textContent=pages.length>1?"Page "+(page+1)+" of "+pages.length:"";
      pre.querySelector('mark[data-active="true"]')?.scrollIntoView?.({block:"nearest"});
    }
    function show(row) {
      active=row;
      page=Math.max(0,pages.findIndex(([a,b])=>row.start>=a&&row.start<b));
      draw();
    }
    let cursor=-1;
    const step=d=>{if(!locations.length)return;cursor=(cursor+d+locations.length)%locations.length;show({...locations[cursor],id:"l"+cursor});
      position.textContent=(cursor+1)+" of "+locations.length;};
    const position=h("span",{...test("release-finding-position"),class:"ws-hint"});
    let query="";
    const find=field("Find",query,v=>{
      query=v;found=[];findIndex=-1;
      if(v){const lower=text.toLowerCase(),q=v.toLowerCase();for(let at=lower.indexOf(q);at>=0&&found.length<1000;at=lower.indexOf(q,at+1))found.push([at,at+q.length]);}
      findCount.textContent=v?found.length+" match"+(found.length===1?"":"es"):"";active=null;draw();
    });
    const findCount=h("span",{class:"ws-hint",...test("release-find-count")});
    const findStep=d=>{if(!found.length)return;findIndex=(findIndex+d+found.length)%found.length;show({kind:"find",start:found[findIndex][0],end:found[findIndex][1],id:"f"+findIndex});};
    const controls=h("div",{class:"row wf-release-tools"},
      button("Previous finding",()=>step(-1),"release-prev",!locations.length),button("Next finding",()=>step(1),"release-next",!locations.length),position,
      find,button("Previous match",()=>findStep(-1),"release-find-prev"),button("Next match",()=>findStep(1),"release-find-next"),findCount,
      pages.length>1?button("Previous page",()=>{page=Math.max(0,page-1);draw();},"release-page-prev"):null,
      pages.length>1?button("Next page",()=>{page=Math.min(pages.length-1,page+1);draw();},"release-page-next"):null,pageLabel);
    draw();
    // Findings, destinations and the decision stay outside the scrolling payload.
    const side=h("div",{class:"wf-release-side"});
    box.append(h("div",{class:"wf-release-grid"},h("div",{class:"wf-release-main"},copyControl("wf-copy-release",()=>review.payload,null),controls,pre),side));
    side.append(h("p",{text:"Review expires "+new Date(review.expires_at*1000).toLocaleString()}));
    const destinationList=h("ul",{class:"wf-destinations",...test("release-destinations")});
    side.append(h("h4",{text:"Downstream destinations"}),destinationList);
    for(const [node,row] of entries(review.destinations)) {
      const item=h("li",{...test("release-destination"),"data-node":node,text:node+" · "+(row.kind==="mcp"?"MCP · "+row.server+"/"+row.tool:row.kind+" · "+(row.model||""))});
      destinationList.append(item);
      const exactRow=row.kind==="mcp"?{kind:"mcp",url:row.url,server:row.server,tool:row.tool}:{kind:row.kind,url:row.url,model:row.model,provider:row.provider||""};
      wsApi("destination-trust-preview",{destination:exactRow}).then(p=>{
        if(!item.isConnected)return;
        const eligible=p.reason===null||p.reason==="destination_untrusted";
        item.dataset.locality=p.locality||"unknown";item.dataset.eligible=String(eligible);item.dataset.trusted=String(!!p.trusted);
        item.textContent=node+" · Connects to: "+(p.locality==="local"?"local endpoint":"remote endpoint")+" · "+
          (row.kind==="mcp"?"MCP · "+row.server+"/"+row.tool:(row.kind==="openrouter"?"OpenRouter":row.kind==="lmstudio"?"LM Studio":"OpenAI compatible")+(row.model?" · "+row.model:""))+
          " · Local-only data: "+(eligible?"Eligible":"Not eligible")+" · "+(p.trusted?"Trusted":"Not trusted");
      }).catch(()=>{});
    }
    if(!Object.keys(review.destinations||{}).length)destinationList.append(h("li",{text:"No processing after this Gate."}));
    const check=review.identifier_check, assessment=review.assessment;
    const locate=(kind,index)=>{const at=locations.findIndex(row=>row.kind===kind&&row.index===index);if(at>=0){cursor=at;show({...locations[at],id:"l"+at});position.textContent=(at+1)+" of "+locations.length;}};
    side.append(h("h4",{text:"Identifier check"}),h("p",{text:check.status==="not_configured"?"Identifier check not configured":
      check.hits.length?check.hits.length+" flagged matches":"No mapped originals found. This is not proof of anonymity."}),
      h("ul",{class:"wf-hits"},check.hits.map((hit,index)=>h("li",{},h("button",{type:"button",class:"wf-link",...test("release-hit"),"data-index":String(index),
        text:hit.identifier+" · "+JSON.stringify(hit.path)+" · "+hit.part+" · "+hit.start+"–"+hit.end,onclick:()=>locate("hit",index)})))),
      h("h4",{text:"Local AI assessment"}),h("p",{...test("release-assessment"),"data-status":assessment.status,text:
        assessment.status==="not_configured"?"Local AI assessment not configured":assessment.status==="unavailable"?
        "Assessment unavailable · "+assessment.reason:assessment.findings.length?"Review the concerns below":"No concerns identified by the local model. This is not proof of anonymity."}),
      h("ul",{},assessment.findings.map((item,index)=>h("li",{},h("button",{type:"button",class:"wf-link",...test("release-concern"),"data-index":String(index),
        text:item.concern+" · "+item.location+" · "+item.explanation,onclick:()=>locate("finding",index)})))));
    if(releaseAcknowledgements.token!==review.token)releaseAcknowledgements={token:review.token,identifiers:false,assessment:false};
    const needIds=check.hits.length>0, needAI=assessment.status==="unavailable"||assessment.findings.length>0;
    const enabled=()=>!releaseSubmitting&&(!needIds||releaseAcknowledgements.identifiers)&&(!needAI||releaseAcknowledgements.assessment);
    const decide=async decision=>{
      releaseSubmitting=true;
      box.querySelectorAll(".wf-release-decision button").forEach(control=>control.disabled=true);
      try {
        await wsApi("workflow-jobs/"+encodeURIComponent(job.id)+"/release",{
          token:review.token,payload_digest:review.payload_digest,executable_digest:review.executable_digest,
          scope_digest:review.scope_digest,checks_digest:review.checks_digest,decision,
          acknowledge_identifiers:releaseAcknowledgements.identifiers,acknowledge_assessment:releaseAcknowledgements.assessment});
      } finally {releaseSubmitting=false;box.querySelectorAll(".wf-release-decision button").forEach(control=>control.disabled=false);release.disabled=!enabled();}
    };
    const release=button("Review and release",()=>decide("release"),"release-approve",!enabled());
    const decisionBox=h("div",{class:"wf-release-decision"});
    if(needIds)decisionBox.append(toggle("I have reviewed the flagged identifier matches",releaseAcknowledgements.identifiers,v=>{
      releaseAcknowledgements.identifiers=v;release.disabled=!enabled();}));
    if(needAI)decisionBox.append(toggle("I have reviewed the AI concerns or assessment unavailability",releaseAcknowledgements.assessment,v=>{
      releaseAcknowledgements.assessment=v;release.disabled=!enabled();}));
    const reject=button("Reject",()=>decide("reject"),"release-reject",releaseSubmitting);
    // Reject and revise submits the same token-bound rejection and navigates only once the server accepts it.
    const revise=button("Reject and revise",async()=>{await decide("reject");await reviseFrom(review.gate,true);},"release-reject-revise",releaseSubmitting);
    decisionBox.append(h("div",{class:"wf-decision-row"},release,h("span",{class:"ws-hint",...test("release-consequence"),
      text:"Releases this value to the processing listed above, for this run only."})),
      h("div",{class:"wf-decision-row"},reject,revise,h("span",{class:"ws-hint",...test("reject-consequence"),
      text:"Ends this run here. Nothing after this Gate runs."+(batch?" Later examples won't run.":"")})));
    side.append(decisionBox);
    releasePanelToken=review.token;releasePanelElement=box;
    return box;
  }
  // Opens the source revision of the node bound to the Gate's value (or the Gate in Design) with a return target
  // that keeps run inputs and selected examples (CD3.6). Nothing is rebound until the user reviews and saves.
  async function reviseFrom(gateId,afterReject=false) {
    const value=draft?.nodes?.[gateId]?.bindings?.value;
    const node=value?.kind==="node"?value.node:null;
    if(node&&sources[node]?.key&&!draft.nodes[node].gate){
      await detour({node,revise:true},()=>openBlock(sources[node].key,sources[node].revision));
      return;
    }
    view="design";selection={kind:"node",id:gateId};render();
    if(afterReject)report("The run was rejected. Revise the value feeding this Gate, save, then check readiness and run again.");
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
      edit(() => set(value ? {...JSON.parse(value), path:[]} : undefined));
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
    // Workflow I/O contracts are inline definitions; Copy saved contract copies one (no pin). The summary line shows
    // the same readable chip as block ports (UI-AB v2 AB5.1, R4).
    const summary = h("summary", {text:"Contract · "+name, "data-test":"wf-contract-chip", "data-port":name});
    const editor = contracts.editor({test:"wf-contract", text:row.contract, shortcuts:true, onView:(view) => {
      const sum = contracts.summarize(view); summary.textContent = "Contract · "+name+" · "+sum.chip; summary.title = sum.readable;
    }, onChange:(text, projected) => {
      row.contract = text; row.format = projected.format; changed();
    }});
    contractEditors.push(editor);
    return h("details", {}, summary, editor.el, button("Copy saved contract", async () => {
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
  // ===================================================================================================
  // Workflow Design (UI-CD v3 CD1): palette · canvas · inspector over one draft, with List view as the whole
  // design form. Protection, destinations and Gate setup come only from the accepted validation result (§0 rule 2).
  // ===================================================================================================
  const SVGNS="http://www.w3.org/2000/svg";
  const PANE={palette:220,inspector:300,canvas:560,gutter:16,rail:48};
  const capturedKey=id=>{
    const row=draft?.nodes?.[id];
    if(!row)return "";
    if(row.gate)return "gate:"+row.gate.contract;
    if(selections[id])return "sel:"+JSON.stringify(selections[id]);
    return "snap:"+(row.snapshot_id||"");
  };
  function setPorts(id,data){
    if(!data){delete ports[id];delete heldPorts[id];return;}
    putKey(ports,id,data);heldPorts[id]={key:capturedKey(id),ports:data};
  }
  const nodeLayout=()=>Object.fromEntries(entries(layout).filter(([id])=>Object.hasOwn(draft.nodes,id)));
  const ioDefault=(format)=>format==="json"?null:["string"];
  // --- undo (CD1.7): graph edits since load or the last Save; a Save is a checkpoint ---------------------
  function snapshot(){return structuredClone({draft,selections,rebind,ports,layout,sources,heldPorts});}
  function restore(state){
    ({draft,selections,rebind,ports,layout,sources,heldPorts}=structuredClone(state));
    if(selection&&!selectionExists(selection))selection=null;
  }
  function edit(fn,{rerender=true}={}){
    const before=snapshot();
    const result=fn();
    if(result===false)return false;
    undoStack.push(before);if(undoStack.length>100)undoStack.shift();
    redoStack=[];
    changed();
    if(rerender)render();
    return result;
  }
  function undo(){
    if(!undoStack.length)return;
    redoStack.push(snapshot());restore(undoStack.pop());changed();render();
  }
  function redo(){
    if(!redoStack.length)return;
    undoStack.push(snapshot());restore(redoStack.pop());changed();render();
  }
  function clearHistory(){undoStack=[];redoStack=[];}
  // --- validation client (CD1.8): the only caller of workflow-validate ---------------------------------
  function bumpValidation(){
    vgen++;
    if(vstatus.state==="checked"||vstatus.state==="truncated"||vstatus.state==="graph_unavailable")vstatus={...vstatus,stale:true};
    else vstatus={state:"checking"};
    paintValidation();
    clearTimeout(vtimer);
    if(draft&&view==="design")vtimer=setTimeout(()=>validateNow().catch(()=>{}),300);
  }
  async function validateNow() {
    clearTimeout(vtimer);vtimer=null;
    if(!draft)return;
    const mine=vgen, context=vcontext;
    vrequested=mine;
    const body={name:draft.name,file_token:token,generation:mine,editor:structuredClone(draft),
      selections:structuredClone(selections),rebind:rebind?[rebind]:[],layout:nodeLayout()};
    if(vbasis&&token&&examples.some(row=>row.id===vbasis))body.example_id=vbasis;
    let result;
    try {result=await wsApi("workflow-validate",body);}
    catch(e) {
      if(mine!==vgen||context!==vcontext)return;
      vaccepted=null;
      vstatus=e.code==="stale"?{state:"stale"}:e.status&&e.status<500&&e.code?{state:"cannot",code:e.code,location:e.body?.issues?.[0]?.location||""}:{state:"unavailable"};
      paintValidation();
      return;
    }
    if(result.generation!==mine||mine!==vgen||context!==vcontext)return;
    let portsChanged=false;
    for(const [node,data] of entries(result.ports)) {
      if(!Object.hasOwn(draft.nodes,node)||draft.nodes[node].gate)continue;
      if(JSON.stringify(Object.keys(data.inputs||{}))+JSON.stringify(Object.keys(data.outputs||{}))!==
         JSON.stringify(Object.keys(ports[node]?.inputs||{}))+JSON.stringify(Object.keys(ports[node]?.outputs||{})))portsChanged=true;
      setPorts(node,data);
    }
    // `ports` is omitted under the size limit: keep the last projection held for an unchanged capture only.
    for(const node of Object.keys(draft.nodes))
      if(!draft.nodes[node].gate&&heldPorts[node]&&heldPorts[node].key!==capturedKey(node)){delete heldPorts[node];}
    vaccepted=result;
    vstatus={state:!result.available?"graph_unavailable":result.truncated?"truncated":"checked"};
    if(portsChanged)renderDesignSurface();
    paintValidation();
  }
  // The last accepted result stays on screen while a newer check is pending, marked stale on the design surface.
  const restriction=()=>vaccepted?.available&&vaccepted.restriction?.available&&!vaccepted.restriction.truncated&&
    ["checked","truncated"].includes(vstatus.state)?vaccepted.restriction:null;
  const accepted=()=>vaccepted&&["checked","truncated","graph_unavailable"].includes(vstatus.state)?vaccepted:null;
  const valueKey=(binding)=>!binding?"":binding.kind==="node"?"node."+binding.node+"."+binding.port:binding.kind==="input"?"input."+binding.port:"constant."+binding.name;
  function valueState(key) {
    const r=restriction(), row=r?.values?.[key];
    if(!row)return {state:"unchecked"};
    if(row.restricted)return {state:"restricted",cause:row.causes?.[0]};
    if(row.causes?.[0]?.code==="released")return {state:"released",cause:row.causes[0]};
    return {state:"open"};
  }
  function causeWords(cause) {
    if(!cause)return "";
    if(cause.code==="upstream"&&Array.isArray(cause.chain)){
      const end=cause.chain.at(-1);
      return "derived from "+(end?.[0]==="context"?"protected step "+end[1]:end?.[0]==="input"?end[1]:end?.slice(1).join("."));
    }
    if(cause.code==="workflow_io")return "workflow "+cause.side.replace(/s$/,"")+" "+cause.port+" is Local only";
    if(cause.code==="declared_label")return "labelled Local only";
    if(cause.code==="consumer_label")return "a receiver is Local only";
    if(cause.code==="example_restriction")return "private in the selected example";
    return (typeof MESSAGES!=="undefined"&&MESSAGES?.codes?.[cause.code]?.title)||cause.code;
  }
  const STATE_TEXT={restricted:"Stays local",released:"Released after your approval on each run",open:"Not restricted",unchecked:"not checked"};
  function stateChip(info,id) {
    const text=STATE_TEXT[info.state]+(info.state==="restricted"&&info.cause?" · "+causeWords(info.cause):"");
    return h("span",{class:"wf-state",...(id?test(id):{}),"data-state":info.state,text});
  }
  function statusView() {
    const s=vstatus;
    const basis=vbasis?"Example: "+(examples.find(row=>row.id===vbasis)?.label||vbasis):"Base labels";
    const text=s.stale||s.state==="checking"?"Checking…":s.state==="checked"?"Design checked · "+basis:
      s.state==="truncated"?"Design checked (details too large)":s.state==="graph_unavailable"?"Validation unavailable":
      s.state==="stale"?"Saved workflow changed elsewhere":s.state==="cannot"?"Can't check yet"+(s.location?" · "+s.location:""):
      s.state==="unavailable"?"Validation unavailable":"Not checked";
    const out=h("span",{class:"wf-validation",...test("validation"),"data-state":s.stale?"checking":s.state,text});
    if(s.state==="stale"&&!s.stale)out.append(" ",button("Reload",async()=>{
      if(dirty&&await dialog("Reload workflow",h("p",{text:"Discard your unsaved changes and reload the saved workflow?"}),["Reload","Cancel"])!=="Reload")return;
      await reloadSaved();},"validation-reload"));
    if(s.state==="unavailable"&&!s.stale)out.append(" ",button("Retry",()=>{bumpValidation();},"validation-retry"));
    if(s.state==="graph_unavailable"&&vaccepted?.unplannable){
      const cycle=vaccepted.unplannable.cycle||[], unknown=vaccepted.unplannable.unknown_source||[];
      out.append(h("span",{class:"ws-hint",...test("unplannable"),text:" · "+[cycle.length?"Cycle: "+cycle.join(" → "):"",unknown.length?"Unknown source: "+unknown.join(", "):""].filter(Boolean).join(" · ")}));
    }
    return out;
  }
  function issueRows() {
    const result=accepted();
    return result?(result.structural||[]):[];
  }
  function destinationRemedies() {
    const result=accepted();
    return entries(result?.destinations).filter(([,row])=>row.remedy).map(([node,row])=>({node,row}));
  }
  // Every validation-derived element is a slot repainted in place, so typing in the inspector keeps focus.
  function vslot(fn,tag="div") {
    const el=h(tag,{"data-vslot":""});
    el._paint=()=>{const out=fn();el.replaceChildren(...(out==null?[]:Array.isArray(out)?out.filter(Boolean):[out]));};
    el._paint();
    return el;
  }
  function paintValidation() {
    if(!main.isConnected)return;
    const host=main.querySelector('[data-test="wf-design-area"]');
    if(host)host.dataset.stale=String(!!vstatus.stale||vstatus.state==="checking");
    for(const el of main.querySelectorAll("[data-vslot]"))el._paint?.();
    paintCanvasStates();
  }
  function remedyButton(node,row) {
    const remedy=row.remedy;
    if(remedy.action==="grant"&&remedy.target)return button("Grant",async()=>{
      if(await wsGrantTrust(trustTarget(remedy.target))){invalidatePlan();bumpValidation();}},"design-grant");
    if(remedy.action==="add_release_gate")return button("Add Release Gate",()=>selectFeedingEdge(node),"design-add-gate");
    if(["choose_local_role","configure_role"].includes(remedy.action))return button(remedyLabel(remedy)||"Open roles",()=>openSettingsAt("roles",node),"design-remedy");
    if(remedy.action==="configure_server")return button(remedyLabel(remedy)||"Open MCP servers",()=>openSettingsAt("servers",node),"design-remedy");
    return h("span",{class:"ws-hint",...test("design-remedy-text"),"data-action":remedy.action,text:messageText({action:remedy.action,target:remedy.target})});
  }
  function trustTarget(d) {
    return d.kind==="mcp"?{kind:"mcp",url:d.url,server:d.server,tool:d.tool}:{kind:d.kind,url:d.url||d.base_url,model:d.model,provider:d.provider||""};
  }
  async function openSettingsAt(category,node) {
    const origin=document.activeElement;
    if(typeof openSettings==="function")await openSettings(category);
    else if(category==="servers")openServers();
    if(origin?.isConnected)origin.focus();
    invalidatePlan();bumpValidation();
    return node;
  }
  function issuePanel() {
    const rows=issueRows(), remedies=destinationRemedies();
    if(!rows.length&&!remedies.length)return h("p",{class:"ws-hint",text:accepted()?"No issues.":"Not checked."});
    const truncated=vaccepted?.truncated;
    return h("div",{},truncated?h("p",{class:"ws-hint",text:"Showing the first 100 issues."}):null,
      h("ul",{class:"wf-findings",...test("issue-list")},rows.map(row=>h("li",{class:"wf-finding",...test("issue"),"data-code":row.code||"","data-severity":row.severity||"error",
        "data-location":row.location||""},h("button",{type:"button",class:"wf-link",...test("issue-select"),text:(row.location||"workflow")+" · "+messageText(row,row.message||row.code),
          onclick:()=>selectLocation(row.location)}),codeDetails(row.code||"issue","wf-issue-code"),
          row.remedy?.action==="add_release_gate"?button("Add Release Gate",()=>selectFeedingEdge(nodeOf(row.location)),"issue-add-gate"):null)),
        remedies.map(({node,row})=>h("li",{class:"wf-finding",...test("destination-remedy"),"data-node":node,"data-action":row.remedy.action},
          h("span",{text:node+" · "+destinationText(row)+" · "}),remedyButton(node,row)))));
  }
  function selectLocation(location) {
    const match=/^nodes\.([^.]+)(?:\.(inputs|outputs)\.([^.]+))?/.exec(location||"");
    if(match&&Object.hasOwn(draft.nodes,match[1])){
      const [,node,side,port]=match;
      if(side==="inputs"&&draft.nodes[node].bindings?.[port])selection={kind:"edge",target:{kind:"node",node,port}};
      else selection={kind:"node",id:node};
    } else {
      const io=/^(inputs|outputs|constants)\.([^.]+)/.exec(location||"");
      selection=io&&Object.hasOwn(draft[io[1]],io[2])?{kind:"io",side:io[1],name:io[2]}:null;
    }
    renderDesignSurface();
  }
  function selectFeedingEdge(node) {
    view="design";
    const row=draft.nodes[node];
    if(!row){render();return;}
    const r=restriction();
    const names=Object.keys(row.bindings||{});
    const port=names.find(name=>valueState(valueKey(row.bindings[name])).state==="restricted")||names.find(name=>row.bindings[name].kind==="node")||names[0];
    selection=port?{kind:"edge",target:{kind:"node",node,port}}:{kind:"node",id:node};
    if(!r)bumpValidation();
    render();
    main.querySelector('[data-test="wf-insert-gate"]')?.focus();
  }
  function destinationText(row) {
    if(!row?.available)return row?.reason==="role_unresolved"?"Role not configured":row?.reason==="server_missing"?"Server not configured":"Destination unresolved";
    const d=row.destination||{};
    const what=d.kind==="mcp"?"MCP · "+d.server+"/"+d.tool:(d.kind==="openrouter"?"OpenRouter":d.kind==="lmstudio"?"LM Studio":"OpenAI compatible")+(d.model?" · "+d.model:"");
    return (d.kind==="mcp"?"":row.locality==="local"?"local endpoint · ":"remote endpoint · ")+what;
  }
  function destinationChip(node,optional=false) {
    const result=accepted(), row=result?.destinations?.[node];
    if(!result&&optional)return null;
    if(!result)return h("span",{class:"wf-chip",...test("chip-connects"),"data-state":"unchecked",text:"Connects to: not checked"});
    if(!row)return null;
    return h("span",{class:"wf-chip",...test("chip-connects"),"data-state":row.available?row.locality||"unknown":"unresolved",
      "data-trusted":String(!!row.trusted),title:"Expected; confirmed at readiness",
      text:"Connects to: "+destinationText(row)+(row.available?" · "+(row.trusted?"Trusted":"Not trusted")+" (expected)":"")});
  }
  function protectionChip(node) {
    const r=restriction();
    if(!r)return h("span",{class:"wf-chip",...test("chip-restriction"),"data-state":"unchecked",text:"Run restriction: not checked"});
    if(!r.nodes?.[node]?.protected)return null;
    return h("span",{class:"wf-chip wf-chip-protected",...test("chip-restriction"),"data-state":"protected",text:"🔒 Protected"});
  }
  // --- palette (CD1.2, CD1.12) ------------------------------------------------------------------------
  async function loadPalette(force=false) {
    if(palette.blocks&&!force||palette.loading)return;
    palette.loading=true;
    let blocks;
    try {blocks=(await api("/api/blocks")).blocks;}
    finally {palette.loading=false;}
    palette.blocks=blocks;palette.details=new Map();
    for(const block of blocks.slice(0,128)) {
      try {
        const base="workflow-source-options/"+encodeURIComponent(block.name);
        const listing=await wsApi(base);
        const newest=listing.revisions?.[0];
        const data=newest?await wsApi(base+"?revision="+encodeURIComponent(newest.id)):listing;
        palette.details.set(block.name,{...data,revisions:listing.revisions||[]});
      } catch {palette.details.set(block.name,{error:true});}
      if(main.isConnected)main.querySelector('[data-test="wf-palette-blocks"]')?._paint?.();
    }
  }
  function blockEligibility(detail) {
    if(!detail)return {ok:false,reason:"Loading…"};
    if(detail.error)return {ok:false,reason:"Saved source unavailable"};
    if(detail.block?.kind==="script")return {ok:false,reason:"Python script blocks can't join workflows"};
    if(detail.version!==4)return {ok:false,reason:"Upgrade in Blocks"};
    return {ok:true};
  }
  function portList(block) {
    const rows=[...entries(block?.inputs).map(([name,port])=>"In · "+name+" · "+port.format),...entries(block?.outputs).map(([name,port])=>"Out · "+name+" · "+port.format)];
    return h("details",{class:"wf-palette-ports"},h("summary",{text:"Ports"}),h("ul",{},rows.map(text=>h("li",{text}))));
  }
  function uniqueId(base,existing) {
    const clean=(base||"node").replace(/[^A-Za-z0-9_]/g,"_").replace(/^([^A-Za-z_])/,"_$1");
    let id=clean,n=2;
    while(Object.hasOwn(existing,id))id=clean+"_"+n++;
    return id;
  }
  async function addBlockNode(key,point) {
    if(rebind)return;
    const detail=palette.details.get(key);
    if(!blockEligibility(detail).ok)return;
    const revisions=detail.revisions||[];
    let revision=revisions[0]?.id||"", id=uniqueId(key,draft.nodes);
    const box=h("div",{...test("add-block")});
    const msg=h("p",{role:"status"});
    const pick=select("Exact revision",revisions.map(row=>[row.id,"Revision "+row.number+(row.title?" · "+row.title:"")]),revision,v=>revision=v,"add-revision");
    box.append(revisions.length?pick:h("p",{text:"Current definition"}),field("Node identifier",id,v=>id=v),msg);
    box.addEventListener("keydown",e=>{if(e.key==="Enter"&&e.target.tagName!=="TEXTAREA"){e.preventDefault();box.closest("dialog")?.querySelector('[data-test="ws-add"]')?.click();}});
    let chosen=null;
    const choice=await dialog("Add "+(palette.blocks.find(b=>b.name===key)?.display_name||key),box,["Add","Cancel"],async action=>{
      if(action!=="Add")return true;
      if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(id)||Object.hasOwn(draft.nodes,id)){msg.textContent="Use a unique identifier starting with a letter or underscore.";return false;}
      try {
        chosen=revision&&revision!==revisions[0]?.id?await wsApi("workflow-source-options/"+encodeURIComponent(key)+"?revision="+encodeURIComponent(revision)):detail;
      } catch(e) {msg.textContent=e.message;return false;}
      if(!blockEligibility(chosen).ok){msg.textContent=blockEligibility(chosen).reason;return false;}
      return true;
    });
    if(choice!=="Add"||!chosen)return;
    edit(()=>{
      putKey(draft.nodes,id,{bindings:{}});
      putKey(selections,id,chosen.selection);
      putKey(sources,id,{key:chosen.selection.key,title:palette.blocks.find(b=>b.name===key)?.display_name||key,revision:chosen.selection.revision});
      setPorts(id,chosen.block);
      putKey(layout,id,point||freeSlot());
      selection={kind:"node",id};
    });
  }
  function freeSlot() {
    const occupied=new Set(Object.values(layout).map(({x,y})=>x+","+y));
    for(let slot=0;;slot++){
      const point={x:(slot%4)*220,y:Math.floor(slot/4)*130};
      if(!occupied.has(point.x+","+point.y))return point;
    }
  }
  async function addIo(side) {
    const singular={inputs:"input",outputs:"output",constants:"constant"}[side];
    const name=await newKey("Add "+singular,draft[side]);
    if(!name)return;
    edit(()=>{
      putKey(draft[side],name,side==="constants"?{value:'""',local_only:false}:{contract:'{"format":"text"}',format:"text",local_only:false});
      selection={kind:"io",side,name};
    });
  }
  function paletteView(compact) {
    const add=(label,id,fn,disabled=false,icon="")=>{
      const b=button(compact?icon||label[0]:label,fn,id,disabled);
      if(compact){b.title=label;b.setAttribute("aria-label",label);}
      return b;
    };
    const essentials=h("div",{class:"wf-palette-group"},
      add("Create block…","create-block",()=>detour({create:true},()=>newBlock?.()),false,"＋"),
      compact?null:h("h4",{text:"Workflow I/O"}),
      add("Input","add-input",()=>addIo("inputs"),!!rebind,"⇥"),
      add("Output","add-output",()=>addIo("outputs"),!!rebind,"⇤"),
      add("Constant","add-constant",()=>addIo("constants"),!!rebind,"＝"),
      compact?null:h("h4",{text:"Built-in"}),
      add("Release Gate","add-gate",()=>addGate(),!!rebind,"🛡"),
      add("Restore placeholders — built-in local transform","create-restore",()=>detour({create:true},()=>newBlock?.("restore_placeholders")),false,"↺"),
      compact?null:h("h4",{text:"Blocks"}),
      add("Choose block…","add-node",()=>pickSource(),!!rebind,"▦"));
    if(compact) {
      const overlay=h("div",{class:"wf-palette-overlay",...test("palette-overlay"),hidden:!paletteOpen});
      if(paletteOpen)overlay.append(paletteBlocks());
      return h("aside",{class:"wf-palette wf-rail",...test("palette"),"data-mode":"rail","aria-label":"Palette"},essentials,
        add("Blocks","palette-toggle",()=>{paletteOpen=!paletteOpen;renderDesignSurface();},false,"☰"),overlay);
    }
    return h("aside",{class:"wf-palette",...test("palette"),"data-mode":"pane","aria-label":"Palette"},
      field("Search blocks",palette.query,(v)=>{palette.query=v;main.querySelector('[data-test="wf-palette-blocks"]')?._paint?.();}),essentials,paletteBlocks());
  }
  function paletteBlocks() {
    const host=vslot(()=>{
      if(!palette.blocks)return h("p",{class:"ws-hint",text:"Loading blocks…"});
      const query=palette.query.toLowerCase();
      const rows=palette.blocks.filter(block=>(block.display_name+" "+block.name+" "+(block.description||"")).toLowerCase().includes(query));
      if(!rows.length)return h("div",{},h("p",{class:"ws-hint",text:palette.blocks.length?"No matching blocks.":"No blocks yet."}),
        palette.blocks.length?null:button("Create block…",()=>detour({create:true},()=>newBlock?.()),"create-block-empty"));
      return h("ul",{class:"wf-palette-list"},rows.map(block=>{
        const detail=palette.details.get(block.name), eligible=blockEligibility(detail);
        const item=h("li",{class:"wf-palette-block",...test("palette-block"),"data-key":block.name,"data-eligible":String(eligible.ok),
          "data-highlight":String(palette.highlight===block.name),tabindex:eligible.ok&&!rebind?"0":"-1",role:"button",
          draggable:eligible.ok&&!rebind?"true":"false","aria-disabled":String(!eligible.ok||!!rebind)},
          h("strong",{text:block.display_name||block.name}),h("span",{class:"ws-hint",text:" · "+block.kind+(detail?.revisions?.[0]?" · rev "+detail.revisions[0].number:"")}),
          block.description?h("p",{class:"wf-palette-desc",text:block.description}):null,
          eligible.ok?portList(detail.block):h("p",{class:"ws-hint",...test("palette-disabled"),text:eligible.reason}));
        if(eligible.ok&&!rebind){
          item.onclick=e=>{if(e.target.closest("details"))return;addBlockNode(block.name).catch(error);};
          item.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();addBlockNode(block.name).catch(error);}};
          item.ondragstart=e=>{e.dataTransfer.setData("application/x-agentwiz-block",block.name);e.dataTransfer.setData("text/plain",block.name);e.dataTransfer.effectAllowed="copy";};
        }
        return item;
      }));
    });
    host.setAttribute("data-test","wf-palette-blocks");
    return host;
  }
  // --- canvas (CD1.1, CD1.3, CD1.4, CD1.7, CD1.10) ------------------------------------------------------
  function ioPositions(points) {
    const xs=[...points.values()].map(p=>p.x), ys=[...points.values()].map(p=>p.y);
    const left=(xs.length?Math.min(...xs):0)-280, right=(xs.length?Math.max(...xs):0)+280, top=ys.length?Math.min(...ys):0;
    const out=new Map();
    Object.keys(draft.inputs).forEach((name,i)=>out.set("inputs:"+name,{x:left,y:top+i*90}));
    const constants=Object.keys(draft.constants);
    if(constants.length)out.set("constants",{x:left,y:top+Object.keys(draft.inputs).length*90+20});
    Object.keys(draft.outputs).forEach((name,i)=>out.set("outputs:"+name,{x:right,y:top+i*90}));
    return out;
  }
  function edgeList() {
    const list=[];
    for(const [node,row] of entries(draft.nodes))for(const [port,binding] of entries(row.bindings))
      list.push({source:binding,target:{kind:"node",node,port}});
    for(const [name,row] of entries(draft.outputs))if(row.binding)list.push({source:row.binding,target:{kind:"output",name}});
    return list;
  }
  const targetKey=t=>t.kind==="node"?"node:"+t.node+":"+t.port:"output:"+t.name;
  const sourceHandle=b=>b.kind==="node"?"out:"+b.node+":"+b.port:b.kind==="input"?"out:input:"+b.port:"out:constant:"+b.name;
  const targetHandle=t=>t.kind==="node"?"in:"+t.node+":"+t.port:"in:output:"+t.name;
  function bindingOf(target) {return target.kind==="node"?draft.nodes[target.node]?.bindings?.[target.port]:draft.outputs[target.name]?.binding;}
  function selectionExists(sel) {
    if(sel.kind==="node")return Object.hasOwn(draft.nodes,sel.id);
    if(sel.kind==="io")return Object.hasOwn(draft[sel.side],sel.name);
    if(sel.kind==="edge")return !!bindingOf(sel.target);
    return false;
  }
  function sourceContract(binding) {
    if(binding.kind==="node")return ports[binding.node]?.outputs?.[binding.port];
    if(binding.kind==="input"){const row=draft.inputs[binding.port];return row&&{format:row.format,value_types:ioDefault(row.format),contract:row.contract};}
    return {format:"json",value_types:null,contract:null};
  }
  function targetContract(target) {
    if(target.kind==="node")return ports[target.node]?.inputs?.[target.port];
    const row=draft.outputs[target.name];return row&&{format:row.format,value_types:ioDefault(row.format),contract:row.contract};
  }
  const typeWords=port=>port?.format==="text"?"Text":port?.value_types?.length===1?({object:"JSON object",array:"JSON array",string:"JSON string",number:"JSON number",integer:"JSON integer",boolean:"JSON boolean","null":"JSON null"}[port.value_types[0]]||"JSON"):"JSON";
  function connectReason(binding,target) {
    if(target.kind==="node"&&binding.kind==="node"&&(binding.node===target.node||wouldCycle(target.node,binding.node)))return "This would create a cycle";
    if(!compatible(sourceContract(binding),targetContract(target)))return typeWords(sourceContract(binding))+" can't feed a "+typeWords(targetContract(target))+" input";
    return "";
  }
  function connect(binding,target) {
    const reason=connectReason(binding,target);
    if(reason){report(reason);return false;}
    const previous=bindingOf(target);
    edit(()=>{
      const value={...binding,path:[]};
      if(target.kind==="node")putKey(draft.nodes[target.node].bindings,target.port,value);
      else draft.outputs[target.name].binding=value;
      selection={kind:"edge",target};
    });
    if(previous)report("Replaced the previous connection. Undo restores it.");
    return true;
  }
  function removeSelection() {
    const sel=selection;
    if(!sel)return;
    if(sel.kind==="edge")edit(()=>{
      if(sel.target.kind==="node")delete draft.nodes[sel.target.node].bindings[sel.target.port];
      else delete draft.outputs[sel.target.name].binding;
      selection=null;
    });
    else if(sel.kind==="node")removeNode(sel.id);
    else if(sel.kind==="io")removeIo(sel.side,sel.name);
  }
  function removeNode(id) {
    edit(()=>{
      delete draft.nodes[id];delete sources[id];delete ports[id];delete heldPorts[id];delete selections[id];delete layout[id];
      if(rebind===id)rebind=null;
      detach(b=>b?.kind==="node"&&b.node===id);
      if(selection?.kind==="node"&&selection.id===id||selection&&!selectionExists(selection))selection=null;
    });
  }
  function removeIo(side,name) {
    edit(()=>{
      delete draft[side][name];
      detach(b=>b&&(side==="inputs"&&b.kind==="input"&&b.port===name||side==="constants"&&b.kind==="constant"&&b.name===name));
      selection=null;
    });
  }
  function insertGate(sel) {
    const target=sel.target, binding=bindingOf(target);
    const contract=gateContractFor(binding,target);
    if(!contract)return;
    edit(()=>{
      const id=uniqueId("gate",draft.nodes);
      const gate={kind:"release",title:"Review redacted text",contract,max_release_wait_seconds:1800,assessment:{enabled:false}};
      putKey(draft.nodes,id,{gate,bindings:{value:{...binding,path:[...(binding.path||[])]}}});
      putKey(ports,id,gatePorts(gate));
      const points=positions();
      const a=binding.kind==="node"?points.get(binding.node):null, b=target.kind==="node"?points.get(target.node):null;
      const point=a&&b?{x:Math.round((a.x+b.x)/2),y:Math.round((a.y+b.y)/2)+70}:freeSlot();
      putKey(layout,id,point);
      const released={kind:"node",node:id,port:"released",path:[]};
      if(target.kind==="node")putKey(draft.nodes[target.node].bindings,target.port,released);
      else draft.outputs[target.name].binding=released;
      selection={kind:"node",id};
    });
  }
  // The Gate's contract is the exact contract of the value it receives (CD1.6, CD1.14); never a guessed default.
  function gateContractFor(binding,target) {
    if(!binding)return null;
    if(!(binding.path||[]).length){
      if(binding.kind==="constant")return null;
      if(binding.kind==="input")return draft.inputs[binding.port]?.contract||null;
      if(draft.nodes[binding.node]?.gate)return draft.nodes[binding.node].gate.contract;
      const port=ports[binding.node]?.outputs?.[binding.port];
      return typeof port?.contract==="string"?port.contract:null;
    }
    if(target.kind==="output")return draft.outputs[target.name]?.contract||null;
    if(draft.nodes[target.node]?.gate)return draft.nodes[target.node].gate.contract;
    const port=ports[target.node]?.inputs?.[target.port];
    return typeof port?.contract==="string"?port.contract:null;
  }
  // Scan candidates (CD1.6): restricted node outputs flagged as identifier maps, from the producer's siblings
  // and its ancestors. Never bound automatically.
  function scanCandidates(gateId) {
    const value=draft.nodes[gateId]?.bindings?.value;
    if(!value||value.kind!=="node")return {available:true,rows:[]};
    const r=restriction();
    if(!r)return {available:false,rows:[]};
    const nodes=[], seen=new Set(), pending=[value.node];
    while(pending.length){
      const node=pending.pop();
      if(seen.has(node)||!draft.nodes[node])continue;
      seen.add(node);nodes.push(node);
      for(const b of Object.values(draft.nodes[node].bindings||{}))if(b.kind==="node")pending.push(b.node);
    }
    const rows=[];
    for(const node of nodes){
      if(draft.nodes[node].gate)continue;
      const outs=ports[node]?.outputs;
      if(!outs||Object.values(outs).some(port=>typeof port.identifier_map!=="boolean"))return {available:false,rows:[]};
      for(const [port,data] of entries(outs)){
        if(node===value.node&&port===value.port)continue;
        if(data.identifier_map&&r.values?.["node."+node+"."+port]?.restricted)rows.push({node,port});
      }
    }
    return {available:true,rows};
  }
  function scanSuggestion(id) {
    return vslot(()=>{
      const bound=draft.nodes[id]?.bindings?.scan;
      if(bound)return h("p",{...test("scan-state"),"data-state":"bound",text:"Identifier map connected: "+valueKey(bound).replace(/^node\./,"")});
      const found=scanCandidates(id);
      if(!found.available)return h("p",{class:"ws-hint",...test("scan-state"),"data-state":"unavailable",text:"Map candidates unavailable. Check again."});
      if(!found.rows.length)return h("p",{class:"ws-hint",...test("scan-state"),"data-state":"none",
        text:"An identifier map is optional. A de-identification block can expose one as a List of records output with string fields original and placeholder."});
      const use=row=>edit(()=>{putKey(draft.nodes[id].bindings,"scan",{kind:"node",node:row.node,port:row.port,path:[]});});
      if(found.rows.length===1){const row=found.rows[0];
        return h("div",{...test("scan-state"),"data-state":"suggested"},button("Use "+row.node+"."+row.port+" for identifier checking",()=>use(row),"use-scan"));}
      let choice=0;
      return h("div",{...test("scan-state"),"data-state":"choose"},select("Identifier map",found.rows.map((row,i)=>[String(i),row.node+"."+row.port]),"0",v=>choice=Number(v),"scan-choice"),
        button("Use for identifier checking",()=>use(found.rows[choice]),"use-scan"));
    });
  }
  function gateSetup(id) {
    return vslot(()=>{
      const g=accepted()?.gates?.[id];
      if(!g)return h("span",{class:"wf-chip",...test("gate-setup"),"data-state":"unchecked",text:"Setup: not checked"});
      return h("span",{class:"wf-chip",...test("gate-setup"),"data-scan":String(!!g.scan_bound),"data-assessment":g.assessment_role?"on":"off",
        text:(g.scan_bound?"Map connected":"Map not configured")+" · "+(g.assessment_role?"Local assessment: "+g.assessment_role:"Local assessment off")});
    },"span");
  }
  const GATE_NAMES={value:"Text to release",scan:"Identifier map — optional",released:"Approved value"};
  function cardFor(kind,key,title,meta) {
    return h("div",{class:"wf-card wf-card-"+kind,...test("card"),"data-kind":kind,"data-key":key,tabindex:"0",role:"group","aria-label":title},
      h("div",{class:"wf-card-head",...test("card-head"),"data-key":key},h("strong",{text:title}),meta?h("span",{class:"ws-hint",text:" "+meta}):null));
  }
  function portRow(side,handleId,label,format,stateFn,extra) {
    const handle=h("button",{type:"button",class:"wf-handle wf-handle-"+side,...test("handle"),"data-handle":handleId,"data-side":side,
      "aria-label":(side==="in"?"Input ":"Output ")+label});
    const row=h("div",{class:"wf-port wf-port-"+side,...test("port"),"data-handle":handleId},
      side==="in"?handle:null,h("span",{class:"wf-port-name",text:label}),h("span",{class:"wf-format",text:format}),
      h("span",{class:"wf-port-state","data-state-for":handleId}),extra||null,side==="out"?handle:null);
    row._state=stateFn;
    return {row,handle};
  }
  function consumersOf(binding) {
    const key=valueKey(binding);
    return edgeList().filter(e=>valueKey(e.source)===key).length;
  }
  function canvasView() {
    const pane=h("div",{class:"wf-canvas-pane",...test("canvas-pane"),tabindex:"0","aria-label":"Workflow canvas",role:"application"});
    const world=h("div",{class:"wf-world",...test("canvas")});
    const svg=document.createElementNS(SVGNS,"svg");svg.setAttribute("class","wf-edges");svg.setAttribute("aria-hidden","true");
    svg.innerHTML='<defs><marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker></defs>';
    world.append(svg);pane.append(world);
    const handles=new Map(), cards=new Map();
    const points=positions(), io=ioPositions(points);
    const place=(card,point)=>{card.style.left=point.x+"px";card.style.top=point.y+"px";};
    // Node cards
    for(const [id,point] of points) {
      const row=draft.nodes[id], detail=ports[id], src=sources[id];
      const card=cardFor(row.gate?"gate":"node",id,row.gate?"🛡 "+(row.gate.title||"Release Gate"):(src?.title||selections[id]?.key||id),id);
      card.dataset.node=id;
      if(row.gate)card.append(h("p",{class:"wf-card-note",text:"Inputs stay local; only the approved value is released, after your decision on each run."}),gateSetup(id),
        vslot(()=>[destinationChip(id,true),protectionChip(id)]));
      else card.append(h("p",{class:"wf-card-meta",text:(src?.revision_number?"Revision "+src.revision_number:selections[id]?"Selected revision":"Captured revision")+
        (detail?.role?" · "+detail.role:detail?.tool?" · "+detail.server+"/"+detail.tool:"")}),
        vslot(()=>[destinationChip(id),protectionChip(id)]));
      card.append(vslot(()=>{const n=issueRows().filter(r=>nodeOf(r.location)===id).length;return n?h("span",{class:"wf-badge",...test("card-issues"),"data-count":String(n),text:n+" issue"+(n===1?"":"s")}):null;},"span"));
      if(!detail)card.append(h("p",{class:"ws-hint",text:"Port details unavailable."}));
      for(const [name,port] of entries(detail?.inputs)) {
        const label=row.gate?GATE_NAMES[name]||name:name;
        const {row:r,handle}=portRow("in","in:"+id+":"+name,label,port.format,()=>{const b=row.bindings?.[name];return b?valueState(valueKey(b)):null;});
        if(row.gate)r.title=name;
        handles.set("in:"+id+":"+name,{handle,target:{kind:"node",node:id,port:name},contract:port});
        card.append(r);
      }
      for(const [name,port] of entries(detail?.outputs)) {
        const binding={kind:"node",node:id,port:name};
        const n=consumersOf(binding);
        const {row:r,handle}=portRow("out","out:"+id+":"+name,row.gate?GATE_NAMES[name]||name:name,port.format,()=>valueState(valueKey(binding)),
          n>1?h("span",{class:"wf-consumers",...test("consumers"),"data-count":String(n),text:"×"+n}):null);
        handles.set("out:"+id+":"+name,{handle,source:binding,contract:port});
        card.append(r);
      }
      place(card,point);cards.set("node:"+id,card);world.append(card);
      dragCard(card,id,points);
    }
    // Workflow I/O cards (derived positions; not stored)
    for(const [name,row] of entries(draft.inputs)) {
      const card=cardFor("input","inputs:"+name,"Input · "+name,row.local_only?"Local only":"");
      card.dataset.side="inputs";card.dataset.name=name;
      const binding={kind:"input",port:name};
      const {row:r,handle}=portRow("out","out:input:"+name,name,row.format,()=>valueState(valueKey(binding)));
      handles.set("out:input:"+name,{handle,source:binding,contract:sourceContract(binding)});
      card.append(r);place(card,io.get("inputs:"+name));cards.set("inputs:"+name,card);world.append(card);
    }
    if(Object.keys(draft.constants).length) {
      const card=cardFor("constants","constants","Constants","");
      const body=h("details",{},h("summary",{text:Object.keys(draft.constants).length+" constant"+(Object.keys(draft.constants).length===1?"":"s")}));
      for(const name of Object.keys(draft.constants)) {
        const binding={kind:"constant",name};
        const {row:r,handle}=portRow("out","out:constant:"+name,name,draft.constants[name].value.startsWith('"')?"text":"json",()=>valueState(valueKey(binding)));
        r.dataset.constant=name;
        handles.set("out:constant:"+name,{handle,source:binding,contract:sourceContract(binding)});
        body.append(r);
      }
      body.open=constantsOpen;body.ontoggle=()=>{constantsOpen=body.open;requestAnimationFrame(()=>drawEdges());};
      card.append(body);place(card,io.get("constants"));cards.set("constants",card);world.append(card);
    }
    for(const [name,row] of entries(draft.outputs)) {
      const card=cardFor("output","outputs:"+name,"Output · "+name,row.local_only?"Local only":"");
      card.dataset.side="outputs";card.dataset.name=name;
      const target={kind:"output",name};
      const {row:r,handle}=portRow("in","in:output:"+name,name,row.format,()=>row.binding?valueState(valueKey(row.binding)):null);
      handles.set("in:output:"+name,{handle,target,contract:targetContract(target)});
      card.append(r);place(card,io.get("outputs:"+name));cards.set("outputs:"+name,card);world.append(card);
    }
    // Tab order follows the visual order of the cards (CD1.10).
    const byPosition=(a,b)=>parseInt(a.style.top)-parseInt(b.style.top)||parseInt(a.style.left)-parseInt(b.style.left);
    for(const card of [...cards.values()].sort(byPosition))world.append(card);
    canvasState={pane,world,svg,handles,cards};
    // selection highlighting
    for(const [key,card] of cards){
      const selected=selection&&(selection.kind==="node"&&key==="node:"+selection.id||selection.kind==="io"&&key===selection.side+":"+selection.name);
      card.dataset.selected=String(!!selected);
      card.onclick=e=>{
        if(e.target.closest(".wf-handle")||e.target.closest("details>summary"))return;
        if(key==="constants"){selection=null;}
        else if(key.startsWith("node:"))selection={kind:"node",id:key.slice(5)};
        else {const [side,name]=key.split(":");selection={kind:"io",side,name};}
        refreshSelection();
      };
    }
    wireHandles(handles);
    pane.addEventListener("keydown",canvasKeys);
    panZoom(pane,world);
    pane.ondragover=e=>{if([...e.dataTransfer.types].includes("application/x-agentwiz-block")){e.preventDefault();e.dataTransfer.dropEffect="copy";}};
    pane.ondrop=e=>{
      const key=e.dataTransfer.getData("application/x-agentwiz-block");
      if(!key)return;
      e.preventDefault();
      addBlockNode(key,worldPoint(e.clientX,e.clientY)).catch(error);
    };
    applyViewport();
    requestAnimationFrame(()=>paintCanvasStates());
    return pane;
  }
  function worldPoint(clientX,clientY) {
    const rect=canvasState.pane.getBoundingClientRect();
    const bound=n=>Math.max(-100000,Math.min(100000,Math.round(n)));
    return {x:bound((clientX-rect.left-viewport.x)/viewport.zoom-100),y:bound((clientY-rect.top-viewport.y)/viewport.zoom-20)};
  }
  function applyViewport() {
    if(!canvasState?.world)return;
    canvasState.world.style.transform="translate("+viewport.x+"px,"+viewport.y+"px) scale("+viewport.zoom+")";
    canvasState.pane.dataset.zoom=String(Math.round(viewport.zoom*100));
  }
  // Handle centre in world coordinates, measured from the rendered port rows (C-F3).
  function centre(el) {
    const w=canvasState.world.getBoundingClientRect(), r=el.getBoundingClientRect();
    return {x:(r.left+r.width/2-w.left)/viewport.zoom,y:(r.top+r.height/2-w.top)/viewport.zoom};
  }
  function visibleHandle(id) {
    const entry=canvasState.handles.get(id);
    if(!entry)return null;
    if(entry.handle.offsetParent)return entry.handle;
    const card=entry.handle.closest(".wf-card");
    return card?.querySelector("summary")||null;
  }
  function drawEdges() {
    if(!canvasState?.svg?.isConnected)return;
    const svg=canvasState.svg;
    for(const old of svg.querySelectorAll("g"))old.remove();
    for(const e of edgeList()) {
      const a=visibleHandle(sourceHandle(e.source)), b=visibleHandle(targetHandle(e.target));
      if(!a||!b)continue;
      const p=centre(a), q=centre(b), dx=Math.max(40,Math.abs(q.x-p.x)/2);
      const d=`M${p.x} ${p.y} C${p.x+dx} ${p.y} ${q.x-dx} ${q.y} ${q.x} ${q.y}`;
      const g=document.createElementNS(SVGNS,"g");
      const key=targetKey(e.target);
      g.setAttribute("class","wf-edge");g.dataset.test="wf-edge";g.dataset.target=key;g.dataset.source=valueKey(e.source);
      g.dataset.x1=String(Math.round(p.x));g.dataset.y1=String(Math.round(p.y));g.dataset.x2=String(Math.round(q.x));g.dataset.y2=String(Math.round(q.y));
      const hit=document.createElementNS(SVGNS,"path");hit.setAttribute("d",d);hit.setAttribute("class","wf-edge-hit");
      const line=document.createElementNS(SVGNS,"path");line.setAttribute("d",d);line.setAttribute("class","wf-edge-line");line.setAttribute("marker-end","url(#wf-arrow)");
      const title=document.createElementNS(SVGNS,"title");
      const marker=document.createElementNS(SVGNS,"text");marker.setAttribute("x",String((p.x+q.x)/2));marker.setAttribute("y",String((p.y+q.y)/2-4));marker.setAttribute("class","wf-edge-marker");
      g.append(hit,line,marker,title);
      if(e.source.path?.length){const label=document.createElementNS(SVGNS,"text");label.setAttribute("x",String((p.x+q.x)/2));label.setAttribute("y",String((p.y+q.y)/2+12));
        label.setAttribute("class","wf-edge-path");label.textContent="· "+e.source.path.join(".");g.append(label);}
      g.addEventListener("click",()=>{selection={kind:"edge",target:e.target};refreshSelection();});
      svg.append(g);
    }
    paintEdgeStates();
    const all=[...canvasState.cards.values()];
    const maxX=Math.max(800,...all.map(c=>c.offsetLeft+c.offsetWidth+80)), maxY=Math.max(400,...all.map(c=>c.offsetTop+c.offsetHeight+80));
    const minX=Math.min(0,...all.map(c=>c.offsetLeft-80)), minY=Math.min(0,...all.map(c=>c.offsetTop-80));
    svg.setAttribute("viewBox",`${minX} ${minY} ${maxX-minX} ${maxY-minY}`);
    svg.style.left=minX+"px";svg.style.top=minY+"px";svg.style.width=(maxX-minX)+"px";svg.style.height=(maxY-minY)+"px";
  }
  function paintEdgeStates() {
    if(!canvasState?.svg)return;
    for(const g of canvasState.svg.querySelectorAll("g.wf-edge")) {
      const info=valueState(g.dataset.source);
      g.dataset.state=info.state;
      g.dataset.selected=String(selection?.kind==="edge"&&targetKey(selection.target)===g.dataset.target);
      g.querySelector("text.wf-edge-marker").textContent=info.state==="restricted"?"🔒":info.state==="released"?"🛡":"";
      g.querySelector("title").textContent=info.state==="restricted"?"Stays local"+(info.cause?" · "+causeWords(info.cause):""):
        info.state==="released"?STATE_TEXT.released:info.state==="open"?"Not restricted":"Not checked";
    }
  }
  function paintCanvasStates() {
    if(!canvasState?.world?.isConnected)return;
    for(const row of canvasState.world.querySelectorAll(".wf-port")) {
      const info=row._state?.();
      const slot=row.querySelector(".wf-port-state");
      if(!info){slot.replaceChildren();slot.removeAttribute("data-state");continue;}
      slot.dataset.state=info.state;slot.textContent=STATE_TEXT[info.state];
      slot.title=info.state==="restricted"&&info.cause?causeWords(info.cause):"";
    }
    // Validation chips and port labels can change the card layout. Measure the handles
    // after painting them so connections stay attached to their rendered centres.
    drawEdges();
  }
  function refreshSelection() {
    if(listView===true||effectiveList()){renderDesignSurface();return;}
    for(const [key,card] of canvasState?.cards||[])
      card.dataset.selected=String(!!(selection&&(selection.kind==="node"&&key==="node:"+selection.id||selection.kind==="io"&&key===selection.side+":"+selection.name)));
    paintEdgeStates();
    const inspector=main.querySelector('[data-test="wf-inspector"]');
    if(inspector)inspector.replaceWith(inspectorView());
  }
  function dragCard(card,id,points) {
    const head=card.querySelector(".wf-card-head");
    head.dataset.test="wf-canvas-move-"+id;
    let moving=null;
    head.onpointerdown=e=>{
      if(e.button!==0)return;
      const p=points.get(id);
      moving={startX:e.clientX,startY:e.clientY,x:p.x,y:p.y,moved:false};
      head.setPointerCapture(e.pointerId);e.stopPropagation();
    };
    head.onpointermove=e=>{
      if(!moving)return;
      const bound=n=>Math.max(-100000,Math.min(100000,Math.round(n)));
      const next={x:bound(moving.x+(e.clientX-moving.startX)/viewport.zoom),y:bound(moving.y+(e.clientY-moving.startY)/viewport.zoom)};
      moving.moved=moving.moved||next.x!==moving.x||next.y!==moving.y;
      points.set(id,next);card.style.left=next.x+"px";card.style.top=next.y+"px";drawEdges();
    };
    const finish=cancel=>{
      if(!moving)return;
      const start=moving;moving=null;
      const next=points.get(id);
      if(cancel||!start.moved){points.set(id,{x:start.x,y:start.y});card.style.left=start.x+"px";card.style.top=start.y+"px";drawEdges();return;}
      edit(()=>{putKey(layout,id,{x:next.x,y:next.y});selection={kind:"node",id};});
    };
    head.onpointerup=()=>finish(false);
    head.onpointercancel=()=>finish(true);
  }
  function moveSelected(dx,dy) {
    if(selection?.kind!=="node")return;
    const id=selection.id, p=positions().get(id);
    edit(()=>{putKey(layout,id,{x:Math.max(-100000,Math.min(100000,p.x+dx)),y:Math.max(-100000,Math.min(100000,p.y+dy))});});
    canvasState?.cards.get("node:"+id)?.focus();
  }
  // Wiring by click/tap, drag or keyboard (CD1.10); dragging is never required.
  function wireHandles(handles) {
    for(const [id,entry] of handles) {
      const {handle}=entry;
      if(entry.source) {
        handle.onclick=e=>{e.stopPropagation();startConnect(entry);};
        handle.onpointerdown=e=>{
          if(e.pointerType==="mouse"&&e.button!==0)return;
          e.stopPropagation();
          const startX=e.clientX,startY=e.clientY;let dragged=false;
          const move=ev=>{if(Math.hypot(ev.clientX-startX,ev.clientY-startY)>6&&!dragged){dragged=true;startConnect(entry,true);}};
          const up=ev=>{
            window.removeEventListener("pointermove",move);window.removeEventListener("pointerup",up);
            if(!dragged)return;
            const over=document.elementFromPoint(ev.clientX,ev.clientY)?.closest?.('.wf-handle-in, .wf-port-in');
            const targetId=over?.dataset?.handle;
            const target=targetId&&canvasState.handles.get(targetId)?.target;
            if(target&&connecting)finishConnect(target);else cancelConnect();
            suppressClick=true;setTimeout(()=>{suppressClick=false;},0);
          };
          window.addEventListener("pointermove",move);window.addEventListener("pointerup",up);
        };
      } else {
        handle.onclick=e=>{e.stopPropagation();if(suppressClick)return;if(connecting)finishConnect(entry.target);else{selection={kind:"edge",target:entry.target};if(!bindingOf(entry.target))selection=entry.target.kind==="node"?{kind:"node",id:entry.target.node}:{kind:"io",side:"outputs",name:entry.target.name};refreshSelection();}};
      }
      handle.onkeydown=e=>{
        if(e.key==="Enter"&&entry.source&&!connecting){e.preventDefault();e.stopPropagation();startConnect(entry);focusCandidate(0);}
      };
    }
  }
  function compatibleTargets() {
    if(!connecting)return [];
    return [...canvasState.handles.values()].filter(entry=>entry.target&&!connectReason(connecting.source,entry.target));
  }
  function startConnect(entry) {
    if(suppressClick)return;
    if(connecting&&connecting.source===entry.source){cancelConnect();return;}
    connecting={source:entry.source,index:0};
    canvasState.pane.dataset.connecting="true";
    for(const item of canvasState.handles.values())if(item.target){
      const reason=connectReason(entry.source,item.target);
      item.handle.dataset.compatible=String(!reason);item.handle.title=reason;
    }
    entry.handle.dataset.connecting="true";
    report("Choose an input to connect "+valueKey(entry.source).replace(/^node\./,"")+". Esc cancels.");
  }
  function focusCandidate(step) {
    const list=compatibleTargets();
    if(!list.length||!connecting)return;
    connecting.index=((connecting.index+step)%list.length+list.length)%list.length;
    list[connecting.index].handle.focus();
  }
  function finishConnect(target) {
    const source=connecting?.source;
    cancelConnect(false);
    if(!source)return;
    const reason=connectReason(source,target);
    if(reason){
      report(reason);
      const handle=canvasState?.handles.get(targetHandle(target))?.handle;
      if(handle){handle.dataset.refused="true";handle.title=reason;}
      return;
    }
    connect(source,target);
  }
  function cancelConnect(clear=true) {
    connecting=null;
    if(!canvasState)return;
    delete canvasState.pane.dataset.connecting;
    for(const item of canvasState.handles.values()){delete item.handle.dataset.compatible;delete item.handle.dataset.connecting;}
    if(clear)report("");
  }
  const editableTarget=t=>!!t?.closest?.('input, textarea, select, [contenteditable="true"], dialog, .wf-popover');
  function canvasKeys(e) {
    if(editableTarget(e.target)||!e.target.closest?.(".wf-canvas-pane"))return;
    if(connecting){
      if(e.key==="Escape"){e.preventDefault();cancelConnect();return;}
      if(["ArrowDown","ArrowRight"].includes(e.key)){e.preventDefault();focusCandidate(1);return;}
      if(["ArrowUp","ArrowLeft"].includes(e.key)){e.preventDefault();focusCandidate(-1);return;}
      if(e.key==="Enter"){const t=canvasState.handles.get(e.target.dataset?.handle)?.target;if(t){e.preventDefault();finishConnect(t);}return;}
    }
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="z"){e.preventDefault();e.shiftKey?redo():undo();return;}
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="y"){e.preventDefault();redo();return;}
    if(e.key==="Delete"||e.key==="Backspace"){if(selection){e.preventDefault();removeSelection();}return;}
    if(selection?.kind==="node"&&e.key.startsWith("Arrow")&&e.target.classList?.contains("wf-card")){
      e.preventDefault();
      const step=e.shiftKey?50:10;
      moveSelected(e.key==="ArrowLeft"?-step:e.key==="ArrowRight"?step:0,e.key==="ArrowUp"?-step:e.key==="ArrowDown"?step:0);
      return;
    }
    if(e.key==="Enter"&&e.target.classList?.contains("wf-card")){e.target.click();}
  }
  function panZoom(pane) {
    const pointers=new Map();let pan=null,pinch=null;
    pane.addEventListener("pointerdown",e=>{
      if(e.target.closest(".wf-card")||e.target.closest(".wf-edge"))return;
      if(connecting){cancelConnect();}
      if(e.button!==0&&e.pointerType==="mouse")return;
      pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
      if(pointers.size===1)pan={x:e.clientX,y:e.clientY,vx:viewport.x,vy:viewport.y,moved:false};
      if(pointers.size===2){const [a,b]=[...pointers.values()];pinch={d:Math.hypot(a.x-b.x,a.y-b.y),zoom:viewport.zoom};pan=null;}
      pane.setPointerCapture?.(e.pointerId);
    });
    pane.addEventListener("pointermove",e=>{
      if(!pointers.has(e.pointerId))return;
      pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
      if(pinch&&pointers.size===2){const [a,b]=[...pointers.values()];zoomTo(pinch.zoom*Math.hypot(a.x-b.x,a.y-b.y)/pinch.d,(a.x+b.x)/2,(a.y+b.y)/2);return;}
      if(pan){viewport.x=pan.vx+e.clientX-pan.x;viewport.y=pan.vy+e.clientY-pan.y;pan.moved=pan.moved||Math.abs(e.clientX-pan.x)+Math.abs(e.clientY-pan.y)>3;applyViewport();}
    });
    const end=e=>{
      pointers.delete(e.pointerId);
      if(pointers.size<2)pinch=null;
      if(pan&&!pan.moved&&e.type==="pointerup"&&(e.target===pane||e.target.closest(".wf-world")===canvasState.world&&!e.target.closest(".wf-card"))){selection=null;refreshSelection();}
      if(!pointers.size)pan=null;
    };
    pane.addEventListener("pointerup",end);pane.addEventListener("pointercancel",end);
    pane.addEventListener("wheel",e=>{
      e.preventDefault();
      if(e.ctrlKey||e.metaKey)zoomTo(viewport.zoom*Math.exp(-e.deltaY/300),e.clientX,e.clientY);
      else {viewport.x-=e.deltaX;viewport.y-=e.deltaY;applyViewport();}
    },{passive:false});
  }
  function zoomTo(zoom,clientX,clientY) {
    const next=Math.max(.25,Math.min(2,zoom));
    const rect=canvasState.pane.getBoundingClientRect();
    const cx=(clientX??rect.left+rect.width/2)-rect.left, cy=(clientY??rect.top+rect.height/2)-rect.top;
    viewport.x=cx-(cx-viewport.x)*next/viewport.zoom;viewport.y=cy-(cy-viewport.y)*next/viewport.zoom;viewport.zoom=next;
    applyViewport();
  }
  // Fit never zooms below the readable-title level (60%); beyond that, pan shows the rest.
  function fit() {
    if(!canvasState?.cards.size)return;
    const cards=[...canvasState.cards.values()];
    const minX=Math.min(...cards.map(c=>c.offsetLeft)), minY=Math.min(...cards.map(c=>c.offsetTop));
    const maxX=Math.max(...cards.map(c=>c.offsetLeft+c.offsetWidth)), maxY=Math.max(...cards.map(c=>c.offsetTop+c.offsetHeight));
    const rect=canvasState.pane.getBoundingClientRect();
    const zoom=Math.max(.6,Math.min(1,(rect.width-48)/Math.max(1,maxX-minX),(rect.height-48)/Math.max(1,maxY-minY)));
    viewport.zoom=zoom;
    viewport.x=Math.max(24,(rect.width-(maxX-minX)*zoom)/2)-minX*zoom;
    viewport.y=Math.max(24,(rect.height-(maxY-minY)*zoom)/2)-minY*zoom;
    if((maxX-minX)*zoom>rect.width)viewport.x=24-minX*zoom;
    if((maxY-minY)*zoom>rect.height)viewport.y=24-minY*zoom;
    applyViewport();drawEdges();
  }
  // --- inspector (CD1.5) --------------------------------------------------------------------------------
  function explainList(causes) {
    return h("ul",{class:"wf-why"},(causes||[]).map(cause=>h("li",{"data-code":cause.code},
      h("span",{text:messageText(cause,causeWords(cause))}),codeDetails(cause.code,"wf-why-code"))));
  }
  function whyNode(id) {
    return vslot(()=>{
      const r=restriction();
      if(!r)return h("p",{class:"ws-hint",...test("why"),"data-state":"unchecked",text:"Run restriction: not checked."});
      const row=r.nodes?.[id];
      if(!row?.protected)return h("p",{...test("why"),"data-state":"open",text:"This step isn't protected: it may send to any configured destination."});
      return h("div",{...test("why"),"data-state":"protected"},h("h4",{text:"Why this stays local"}),explainList(row.causes));
    });
  }
  function whyValue(key) {
    return vslot(()=>{
      const r=restriction(), row=r?.values?.[key];
      if(!row)return h("p",{class:"ws-hint",...test("why-value"),"data-state":"unchecked",text:"Not checked."});
      const info=valueState(key);
      return h("div",{...test("why-value"),"data-state":info.state},stateChip(info),row.restricted||info.state==="released"?explainList(row.causes):null);
    });
  }
  function ownLabel(port) {
    return port?.local_only===true?"Local only":port?.local_only===false?"No local-only label":"Not set";
  }
  function inspectorView() {
    const box=h("aside",{class:"wf-inspector",...test("inspector"),"aria-label":"Inspector","data-selection":selection?selection.kind:"workflow"});
    const editable=!effectiveList();
    if(selection?.kind==="node"&&draft.nodes[selection.id])box.append(...nodeInspector(selection.id,editable));
    else if(selection?.kind==="edge"&&bindingOf(selection.target))box.append(...edgeInspector(selection.target));
    else if(selection?.kind==="io"&&draft[selection.side]?.[selection.name])box.append(...ioInspector(selection.side,selection.name,editable));
    else box.append(...workflowInspector());
    return box;
  }
  function workflowInspector() {
    const out=[h("h3",{text:"Workflow"})];
    if(!effectiveList()){
      if(!token)out.push(field("Workflow name",draft.name,v=>{draft.name=v;changed();}));
      out.push(field("Purpose",draft.purpose,v=>{draft.purpose=v;changed();},true));
    }
    const budget=h("details",{},h("summary",{text:"Limits"}));
    for(const [key,label,fallback] of [["max_seconds","Seconds",1800],["max_model_attempts","Model attempts",32]])
      budget.append(field(label,draft.budget?.[key]??fallback,v=>{draft.budget??={};draft.budget[key]=/^\d+$/.test(v)?Number(v):null;changed();}));
    const inspect=h("details",{},h("summary",{text:"Inspect exact definition"}),button("Inspect exact definition",async()=>{
      const result=await wsApi("workflow-definition",{editor:draft});
      await dialog("Exact workflow definition",h("pre",{text:result.definition_text}),["Close"]);
    },"inspect-definition"));
    if(examples.length&&token)out.push(select("Check with example",[["","Base labels"],...examples.map(row=>[row.id,row.label||row.id])],vbasis,v=>{vbasis=v;bumpValidation();},"check-example"));
    out.push(budget,inspect);
    if(token)out.push(h("details",{},h("summary",{text:"Delete workflow"}),button("Delete workflow",()=>exclusive(removeWorkflow),"delete-workflow",dirty)));
    return out;
  }
  function nodeInspector(id,editable) {
    const row=draft.nodes[id], detail=ports[id], src=sources[id];
    const out=[h("h3",{...test("inspector-title"),text:row.gate?"Release Gate · "+id:(src?.title||id)}),
      h("p",{class:"ws-hint",text:"Node "+id+(row.gate?"":" · "+(src?.revision_number?"Revision "+src.revision_number:selections[id]?"Selected revision":"Captured revision"))})];
    if(row.gate) {
      out.push(h("p",{text:"Inputs stay local; only the approved value is released, after your decision on each run."}),gateSetup(id));
      const group=h("div");
      if(editable){gateForm(id,row,group);out.push(group,h("h4",{text:"Identifier map"}),scanSuggestion(id));}
    } else {
      // In List view the form owns the source line and its actions; the inspector adds only context.
      if(editable){
        const label=h("p",{...test("source-"+id),text:(src?.revision_number?"Captured revision "+src.revision_number:"Captured source")+
          (detail?.role?" · Role "+detail.role:detail?.tool?" · "+detail.server+"/"+detail.tool:"")});
        const actions=h("div",{class:"row"});out.push(label,actions);
        sourceActions(id,actions,label);
      }
      out.push(vslot(()=>{const r=accepted()?.destinations?.[id];return r?h("p",{...test("destination-facts"),text:destinationText(r)+" · "+(r.trusted?"Trusted":"Not trusted")+
        " · expected; confirmed at readiness"},r.remedy?" ":null,r.remedy?remedyButton(id,r):null):null;}));
    }
    out.push(whyNode(id));
    for(const [name,port] of entries(detail?.inputs)) {
      const label=row.gate?(GATE_NAMES[name]||name):name;
      const binding=row.bindings?.[name];
      const wrap=h("div",{class:"wf-inspector-port",...test("inspector-input"),"data-port":name},h("strong",{text:"Input · "+label+" · "+port.format}),
        vslot(()=>binding?stateChip(valueState(valueKey(binding))):h("span",{class:"ws-hint",text:"Not connected"}),"span"));
      if(editable)wrap.append(bindingForm("Bind "+name+" · "+port.format+(port.local_only?" · Local only":""),binding,v=>setNodeBinding(id,name,v),id));
      wrap.append(h("details",{},h("summary",{text:"This port's own label"}),h("span",{...test("own-label"),text:ownLabel(port)})));
      out.push(wrap);
    }
    for(const [name,port] of entries(detail?.outputs)) {
      const binding={kind:"node",node:id,port:name};
      out.push(h("div",{class:"wf-inspector-port",...test("inspector-output"),"data-port":name},
        h("strong",{text:"Output · "+(row.gate?GATE_NAMES[name]||name:name)+" · "+port.format}),
        vslot(()=>stateChip(valueState(valueKey(binding))),"span"),
        h("span",{class:"ws-hint",text:" · "+consumersOf(binding)+" consumer"+(consumersOf(binding)===1?"":"s")}),
        h("details",{},h("summary",{text:"This port's own label"}),h("span",{...test("own-label"),text:ownLabel(port)}))));
    }
    if(editable){
      out.push(h("div",{class:"row"},button("Rebind "+id,()=>pickSource(id),"rebind-node",!row.snapshot_id||!!rebind||Object.keys(selections).length>0),
        button("Remove "+id,()=>removeNode(id),"remove-node")));
      out.push(layoutForm(id,positions().get(id)));
    }
    return out;
  }
  function sourceActions(id,actions,label) {
    const src=sources[id];
    if(!src?.key)return;
    const marker=generation;
    wsApi("workflow-source-options/"+encodeURIComponent(src.key)+(src.revision?"?revision="+encodeURIComponent(src.revision):""))
      .then(result=>{if(marker===generation&&actions.isConnected) {
        if (!src.revision && src.fingerprint && result.selection?.fingerprint !== src.fingerprint) label.textContent="Workflow snapshot";
        else actions.append(button("Open source revision",()=>detour({node:id},()=>openBlock(src.key,src.revision)),"open-source"));
      }}).catch(()=>{if(marker===generation&&actions.isConnected)label.textContent="Workflow snapshot · saved source unavailable";});
  }
  function edgeInspector(target) {
    const binding=bindingOf(target);
    const from=valueKey(binding).replace(/^node\./,"").replace(/^input\./,"input ").replace(/^constant\./,"constant ");
    const to=target.kind==="node"?target.node+"."+target.port:"output "+target.name;
    const contract=gateContractFor(binding,target);
    const insert=button("Insert Release Gate",()=>insertGate({target}),"insert-gate",!contract||!!rebind);
    const out=[h("h3",{text:"Connection"}),h("p",{...test("edge-summary"),text:from+" → "+to}),whyValue(valueKey(binding)),
      insert,contract?null:h("p",{class:"ws-hint",...test("insert-gate-reason"),text:binding.kind==="constant"&&!(binding.path||[]).length?
        "Constants have no declared contract; insert a Gate by hand.":"Contract details unavailable. Check again."})];
    out.push(pathForm(binding.path||[],path=>{binding.path=path;changed();drawEdges();}));
    out.push(button("Delete connection",()=>removeSelection(),"delete-edge"));
    return out;
  }
  function ioInspector(side,name,editable) {
    const row=draft[side][name];
    const out=[h("h3",{text:{inputs:"Input",outputs:"Output",constants:"Constant"}[side]+" · "+name})];
    if(side!=="outputs")out.push(whyValue(side==="inputs"?"input."+name:"constant."+name));
    else out.push(vslot(()=>row.binding?stateChip(valueState(valueKey(row.binding))):h("span",{class:"ws-hint",text:"No source"}),"span"));
    if(editable)out.push(ioForm(side,name,row));
    else out.push(h("p",{text:"This port's own label: "+(row.local_only?"Local only":"No local-only label")}));
    return out;
  }
  // One I/O item's form, shared by the I/O inspector and List view.
  function ioForm(side,name,row) {
    const textMode = side === "constants" && row.value.startsWith('"');
    return h("div",{class:"wf-item"}, h("strong",{text:name}),
      toggle("Local only",row.local_only,v=>{row.local_only=v;changed();}),
      side === "constants" ? h("div",{}, select("Constant format", [["text","Text"],["json","Exact JSON"]], row.value.startsWith('"') ? "text" : "json", v=>{
        row.value=v==="text"?'""':"null";changed();render();
      }),field("Constant value",row.value.startsWith('"') ? JSON.parse(row.value) : row.value,v=>{
        row.value=textMode?JSON.stringify(v):v;changed();
      },true)) : h("div",{},field("Description",row.description,v=>{row.description=v;changed();}),contractForm(row,name,side)),
      side === "outputs" ? bindingForm("Output source",row.binding,v=>{if(v)row.binding=v;else delete row.binding;}) : null,
      button("Remove "+name,()=>removeIo(side,name),"remove-port"));
  }
  // List view (CD1.1, CD1.10): the whole design form over the same draft, selection and inspector.
  function listForm() {
    const area=h("div",{class:"wf-list",...test("list")});
    // Name and purpose belong to the whole form in List view (the inspector shows them in canvas view only).
    if (!token) area.append(field("Workflow name",draft.name,v=>{draft.name=v;changed();}));
    area.append(field("Purpose",draft.purpose,v=>{draft.purpose=v;changed();},true));
    for (const side of ["inputs","outputs","constants"]) {
      const group = h("fieldset",{},h("legend",{text:side[0].toUpperCase()+side.slice(1)}));
      for (const [name,row] of entries(draft[side])) group.append(ioForm(side,name,row));
      area.append(group);
    }
    const points=positions();
    const nodes=h("fieldset",{},h("legend",{text:"Nodes"}));
    for (const [id,node] of entries(draft.nodes)) {
      const src=sources[id], detail=ports[id];
      const group=h("fieldset",{...test("list-node"),"data-node":id},h("legend",{text:id+" · "+(src?.title || selections[id]?.key || "")}));
      group.onfocusin=()=>{if(selection?.kind!=="node"||selection.id!==id){selection={kind:"node",id};const old=main.querySelector('[data-test="wf-inspector"]');if(old)old.replaceWith(inspectorView());}};
      if(node.gate){gateForm(id,node,group);group.append(scanSuggestion(id));}
      else {
        const label=h("p",{...test("source-"+id),text:(src?.revision_number ? "Captured revision "+src.revision_number : "Captured source")+
          (detail?.role ? " · Role "+detail.role : detail?.tool ? " · "+detail.server+"/"+detail.tool : "")});
        const actions=h("div",{class:"row"});group.append(label,actions);sourceActions(id,actions,label);
      }
      for(const [name,port] of entries(detail?.inputs)) group.append(bindingForm(
        "Bind "+name+" · "+port.format+(port.local_only?" · Local only":""),node.bindings[name],v=>setNodeBinding(id,name,v),id));
      for(const [name,port] of entries(detail?.outputs)) group.append(h("p",{text:"Output "+name+" · "+port.format},
        vslot(()=>stateChip(valueState(valueKey({kind:"node",node:id,port:name}))),"span")));
      group.append(button("Rebind "+id,()=>pickSource(id),"rebind-node",!node.snapshot_id || !!rebind || Object.keys(selections).length>0),
        button("Remove "+id,()=>removeNode(id),"remove-node"));
      group.append(layoutForm(id,points.get(id)));
      nodes.append(group);
    }
    area.append(nodes);
    return area;
  }
  // --- layout bands (§0.1) ------------------------------------------------------------------------------
  function bandFor(width) {
    if(width<720)return "narrow";
    const palettePx=Math.max(PANE.palette,width*.2), inspectorPx=Math.max(PANE.inspector,width*.28);
    if(palettePx+inspectorPx+PANE.canvas+2*PANE.gutter<=width)return "wide";
    // Rail: the palette collapses to icons; if the canvas and inspector minimums still don't fit, the inspector
    // moves below the canvas rather than shrinking either pane.
    return PANE.rail+inspectorPx+PANE.canvas+2*PANE.gutter<=width?"rail":"stacked";
  }
  const effectiveList=()=>listView===null?band==="narrow":listView;
  function designToolbar() {
    const undoButton=button("Undo",undo,"undo",!undoStack.length), redoButton=button("Redo",redo,"redo",!redoStack.length);
    const listToggle=button(effectiveList()?"Canvas":"List view",()=>{listView=!effectiveList();cancelConnect(false);renderDesignSurface();},"list-view");
    listToggle.setAttribute("aria-pressed",String(effectiveList()));
    const issues=vslot(()=>{
      const n=issueRows().length, d=destinationRemedies().length;
      const b=button((accepted()?n:"–")+" issue"+(n===1?"":"s"),()=>{issuesOpen=!issuesOpen;main.querySelector('[data-test="wf-issue-panel"]')?._paint?.();},"issues");
      b.dataset.count=String(n);b.setAttribute("aria-expanded",String(issuesOpen));
      const r=d?button(d+" destination remed"+(d===1?"y":"ies"),()=>{issuesOpen=true;main.querySelector('[data-test="wf-issue-panel"]')?._paint?.();},"destination-remedies"):null;
      if(r)r.dataset.count=String(d);
      return [b,r];
    },"span");
    const runButton=button("Run →",async()=>{
      if(dirty){if(!await exclusive(save))return;}
      view="run-view";render();
    },"to-run",!!(job?.running||pendingStart));
    return h("div",{class:"row wf-design-toolbar",...test("design-toolbar")},vslot(statusView,"span"),issues,undoButton,redoButton,
      effectiveList()?null:button("Fit",fit,"fit"),listToggle,runButton);
  }
  function renderDesignSurface() {
    const host=main.querySelector('[data-test="wf-design-area"]');
    if(!host)return;
    const fresh=design();
    const focused=document.activeElement;
    const focusKey=focused&&host.contains(focused)?focused.dataset?.handle||focused.dataset?.key||focused.dataset?.test:null;
    host.replaceWith(fresh);
    if(focusKey){const again=fresh.querySelector(`[data-handle="${CSS.escape(focusKey)}"], .wf-card[data-key="${CSS.escape(focusKey)}"], [data-test="${CSS.escape(focusKey)}"]`);again?.focus?.();}
  }
  function design() {
    const area=h("div",{class:"wf-design",...test("design-area"),"data-band":band,"data-view":effectiveList()?"list":"canvas"});
    const panelIssues=vslot(()=>issuesOpen?issuePanel():null);panelIssues.dataset.test="wf-issue-panel";
    area.append(designToolbar(),panelIssues);
    const compact=band!=="wide";
    const center=effectiveList()?listForm():canvasView();
    area.append(h("div",{class:"wf-panes"},paletteView(compact),h("div",{class:"wf-center"},center),inspectorView()));
    loadPalette().catch(()=>{});
    if(vrequested!==vgen){clearTimeout(vtimer);vtimer=setTimeout(()=>validateNow().catch(()=>{}),vaccepted?300:0);}
    return area;
  }
  // --- detours and return (CD1.12, CD1.13) ---------------------------------------------------------------
  async function detour(kind,go) {
    window.agentwizWorkflowReturn={workflow:draft?.name||"",dirty,inputs:{...inputs},selected:[...selected],...kind};
    try {await go();}
    catch(e) {window.agentwizWorkflowReturn=null;throw e;}
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
    edit(()=>{
      if(replacement){
        rebind=id;
        for(const name of Object.keys(draft.nodes[id].bindings)) if(!Object.hasOwn(details.block.inputs,name))delete draft.nodes[id].bindings[name];
        detach(b=>b?.kind==="node"&&b.node===id&&!Object.hasOwn(details.block.outputs,b.port));
      } else {putKey(draft.nodes,id,{bindings:{}});putKey(layout,id,freeSlot());}
      putKey(selections,id,details.selection);
      putKey(sources,id,{key:details.selection.key,title:details.selection.key,revision:details.selection.revision});
      setPorts(id,details.block);
      selection={kind:"node",id};
    });
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
      if(e.kind==="text"){
        // Text checks (core v3 §7.2); a row is only rewritten when the author changes it.
        e.assertions.forEach((row,index)=>expectation.append(h("fieldset",{...test("text-check")},h("legend",{text:"Text check "+(index+1)}),
          select("Output",[["","Choose Text output"],...entries(draft.outputs).filter(([,port])=>port.format!=="json").map(([name])=>[name,name])],row.output,v=>row.output=v),
          select("Condition",[["includes","Includes every value"],["excludes","Excludes every value"],["excludes_pattern","No placeholder tokens left"]],row.operation,v=>{
            row.operation=v;
            if(v==="excludes_pattern"){delete row.values;delete row.case;row.pattern="bracket_upper";}
            else{delete row.pattern;row.values=row.values||[];row.case=row.case||"insensitive";}
            drawExpectation();
          }),
          row.operation==="excludes_pattern"?null:field("Values, one per line",(row.values||[]).join("\n"),v=>row.values=v.split("\n").filter(Boolean),true),
          row.operation==="excludes_pattern"?null:select("Letter case",[["insensitive","Ignore case"],["sensitive","Match case"]],row.case,v=>row.case=v),
          button("Remove check",()=>{e.assertions.splice(index,1);drawExpectation();},"remove-text-check"))));
        expectation.append(button("Add text check",()=>{e.assertions.push({output:"",operation:"excludes",values:[],case:"insensitive"});drawExpectation();},"add-text-check"));
      }
    }
    box.append(select("Expectation",[["review","Human review"],["whole_output","Whole outputs"],["fields","Field checks"],["text","Text checks"]],example.expectation.kind,v=>{
      example.expectation=v==="review"?{kind:v,criterion:""}:v==="fields"||v==="text"?{kind:v,assertions:[]}:{kind:v,values:{}};
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
  // Readiness findings (UI-AB v2 AB8.1): one row per issue with a remedy routed by the object that owns it.
  // Privacy-relevant warnings stay visible; only routine notices fold.
  const PRIVACY_CODES=new Set(["unrestricted_ancestor","destination_untrusted","destination_ineligible","destination_proxied","destination_changed","destination_trust_unavailable"]);
  function nodeOf(location){const m=/^nodes\.([^.]+)/.exec(location||"");return m?m[1]:null;}
  function findingRemedies(row,d) {
    const out=[], node=nodeOf(row.location), dest=node?(d.destinations||{})[node]:null;
    if(["destination_untrusted"].includes(row.code)&&dest){
      out.push(button("Grant",async()=>{const exact=dest.kind==="mcp"?{kind:"mcp",url:dest.url,server:dest.server,tool:dest.tool}:{kind:dest.kind,url:dest.url,model:dest.model,provider:dest.provider||""};
        if(await wsGrantTrust(exact))await exclusive(preflight);},"finding-grant"));
    }
    if(["destination_ineligible","destination_proxied"].includes(row.code)&&dest){
      out.push(h("span",{class:"ws-hint",...test("finding-remedy"),text:wsRemedyText(row.code==="destination_proxied"?"proxied":dest.url&&/localhost/.test(dest.url)?"hostname":"remote")}));
    }
    if(row.remedy?.action==="add_release_gate"||row.code==="unrestricted_ancestor"||(node&&["destination_ineligible","destination_untrusted"].includes(row.code)&&row.message?.includes("released value"))){
      // Opens Design with the edge feeding the refused node selected; the user still confirms the insert (CD1.6).
      out.push(button("Add Release Gate",()=>{
        if(node&&draft.nodes[node])selectFeedingEdge(node);else{view="design";render();}
        report("Insert a Release Gate on the selected connection, then save and check readiness again.");},"finding-add-gate"));
    }
    if(node&&sources[node]&&!draft.nodes[node]?.gate&&!/^destination_untrusted$/.test(row.code)){
      out.push(button("Open source revision",()=>detour({node},()=>openBlock(sources[node].key,sources[node].revision)),"finding-open-source"));
    }
    if(!node&&/^(inputs|outputs|constants)\./.test(row.location||""))out.push(button("Edit in workflow",()=>{view="design";render();},"finding-edit-workflow"));
    return out;
  }
  function findingRow(row,d) {
    return h("li",{class:"wf-finding",...test("finding"),"data-code":row.code||"","data-severity":row.severity||"error","data-remedy":row.remedy?.action||""},
      h("strong",{text:(row.severity==="warning"?"Warning":"Blocking")+" · "}),h("span",{text:(row.location||"workflow")+" · "+messageText(row,row.message||row.code)}),
      h("span",{class:"row"},...findingRemedies(row,d)),codeDetails(row.code||"issue","wf-finding-code"));
  }
  function reviewView() {
    const d=plan.descriptor;
    const visible=d.issues.filter(row=>row.severity!=="warning"||PRIVACY_CODES.has(row.code)), routine=d.issues.filter(row=>!visible.includes(row));
    const order=h("p",{text:"Order: "+d.order.join(" → ")});
    return h("div",{},h("h3",{text:"Readiness"}),
      h("div",{...test("readiness-state"),"data-state":d.issues.some(row=>row.severity!=="warning")?"blocked":"ready",
        text:d.issues.some(row=>row.severity!=="warning")?"Resolve the blocking findings before running.":"Ready to run."}),
      visible.length?h("ul",{class:"wf-findings",...test("findings")},visible.map(row=>findingRow(row,d))):null,
      routine.length?h("details",{...test("routine-findings")},h("summary",{text:routine.length+" routine notice"+(routine.length===1?"":"s")}),h("ul",{},routine.map(row=>findingRow(row,d)))):null,
      order,
      h("p",{text:d.repetitions+" planned · "+examples.length+" saved examples · MCP confirmation: "+
        (d.mcp_confirmation.mode==="every_run"?"Every MCP workflow run":"Only MCP batches")}),
      d.mcp_confirmation.invalid?h("p",{text:"The saved preference is invalid. Every MCP workflow run requires confirmation."}):null,
      h("p",{...test("saving-state"),"data-state":d.saving?"on":"off",text:d.saving?"Save runs is on: run evidence can store protected values in unencrypted local files.":"Save runs is off: run evidence stays in this session."}),
      d.issues.some(row=>row.code==="tool_unverified")?
        [...new Set(destinations(d).filter(([,item])=>item.kind==="mcp"&&!item.observation).map(([,item])=>item.server))].map(server=>
          button("Refresh tools · "+server,()=>exclusive(async()=>{
            const config=await api("/api/state");
            await api("/api/mcp/tools",{server,revision:config.mcp.revision});
            await preflight();
          }),"refresh-tools")):null,
      d.restricted_sources?.map(row=>h("p",{text:row.source.join(" · ")+" is local only · "+row.origin})),
      h("ul",{class:"wf-destinations"},destinations(d).map(([node,x])=>h("li",{...test("destination"),"data-node":node,"data-protected":String(!!x.protected),
        text:node+" · "+x.kind+" · "+x.url+" · "+(x.model||x.tool||"")+(x.kind==="mcp"?" · Effects "+x.effects+" · Claim "+x.effect_claim:"")+(x.protected?" · Local-only values":"")}))),
      button("Trusted destinations",()=>settings(destinations(d).map(([node,item])=>({...item,node})),draft.name),"preflight-settings"),
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
    job={id:result.operation_id,name:body.request.name,running:true,batch:!!body.request.examples,total:body.request.examples?.length||0};
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
      // The header activity indicator (CD9) reopens this run's view; it never starts a run.
      activity?.("workflow",current.running?{label:next.phase==="awaiting_release"?"Awaiting your review":"Workflow run",state:next.phase||"running",source:"workflow",
        open:()=>{if(typeof selectTab==="function")selectTab("workflows");showRun();}}:null);
      if(current.running)renderTrace();else render();
      if(current.running)timer=setTimeout(()=>poll(current,epoch),400);
    }catch(e){if(epoch===generation&&current===job){error(e);timer=setTimeout(()=>poll(current,epoch),1800);}}
  }
  // Workflow result first (CD3.7): outputs in definition order with description, effective Local-only state,
  // outcome, Copy and, beside a restored output, a one-line restoration summary read once from node detail.
  function outputRows(outputs, outcome, index) {
    const defined=Object.keys(draft?.outputs||{}), present=outputs||{};
    const order=[...defined.filter(key=>Object.hasOwn(present,key)),...Object.keys(present).filter(key=>!defined.includes(key))];
    if(!order.length)return null;
    return h("div",{class:"wf-outputs",...test("outputs")},order.map(key=>{
      const row=present[key], description=draft?.outputs?.[key]?.description;
      return h("div",{class:"wf-item wf-output",...test("output"),"data-output":key,"data-local-only":String(!!row.local_only)},
        h("strong",{text:key}),h("span",{class:"ws-hint",text:" · "+(row.local_only?"Local only":"Not local-only")+(outcome?" · "+(OUTCOMES[outcome]||outcome):"")}),
        description?h("p",{class:"ws-hint",text:description}):null,
        h("pre",{text:row.text}),copyControl("wf-copy-output",()=>row.text,null),restoreLine(key,index));
    }));
  }
  function restoreDetail(trace) {
    for(const step of trace?.steps||[])for(const fill of step.fills||[])
      if(fill&&typeof fill==="object"&&fill.replacements&&fill.absent&&fill.unknown)return fill;
    return null;
  }
  function restoreSummaryText(detail) {
    const restored=detail.replacements.items.reduce((n,row)=>n+row.count,0), absent=detail.absent.items.length;
    return restored+" placeholder"+(restored===1?"":"s")+" restored."+(absent?" "+absent+" mapping entr"+(absent===1?"y wasn't":"ies weren't")+" present in the summary.":"");
  }
  const restoreCache=new Map();
  function restoreLine(name,index) {
    const binding=draft?.outputs?.[name]?.binding, detail=binding?.kind==="node"?ports[binding.node]:null;
    if(!job||!detail||detail.kind!=="transform"||!detail.inputs?.pairs||!detail.inputs?.text)return null;
    const node=index===undefined?binding.node:index+":"+binding.node, key=job.id+"|"+node;
    const line=h("p",{class:"ws-hint",...test("restore-summary")});
    const fill=value=>{line.dataset.state=value.state;line.textContent=value.text;};
    if(restoreCache.has(key)){fill(restoreCache.get(key));return line;}
    const unavailable={state:"unavailable",text:"Restoration details are unavailable."};
    wsApi("workflow-jobs/"+encodeURIComponent(job.id)+"?node="+encodeURIComponent(node)).then(trace=>{
      const found=restoreDetail(trace), value=found?{state:"available",text:restoreSummaryText(found)}:unavailable;
      restoreCache.set(key,value);fill(value);
    }).catch(()=>{restoreCache.set(key,unavailable);fill(unavailable);});
    return line;
  }
  // Restore details in node detail (CD3.5): absent mapping entries are not a failure and are never reinserted.
  function restoreView(detail) {
    const list=(rows,fn)=>h("ul",{},rows.map(row=>h("li",{text:fn(row)})));
    return h("section",{class:"wf-restore",...test("restore-detail")},h("h4",{text:"Restoration"}),
      h("div",{...test("restore-replacements")},h("p",{text:restoreSummaryText(detail)}),list(detail.replacements.items,row=>row.placeholder+" · "+row.count),
        detail.replacements.truncated?h("p",{class:"ws-hint",text:"List truncated."}):null),
      h("div",{...test("restore-absent"),"data-count":String(detail.absent.items.length)},
        h("p",{text:detail.absent.items.length+" mapping entr"+(detail.absent.items.length===1?"y wasn't":"ies weren't")+" present in the summary. "+
          "A summary may leave details out; restoration only replaces placeholders that survive, and nothing is reinserted."}),
        list(detail.absent.items,row=>String(row)),detail.absent.truncated?h("p",{class:"ws-hint",text:"List truncated."}):null),
      h("div",{...test("restore-unknown"),"data-count":String(detail.unknown.items.length)},
        h("p",{text:detail.unknown.items.length?"Placeholders in the text with no mapping entry (kept):":"No unknown placeholders."}),
        list(detail.unknown.items,row=>row.placeholder+" · offset "+row.offset),detail.unknown.truncated?h("p",{class:"ws-hint",text:"List truncated."}):null));
  }
  // Node and record detail through the shared ResultView (CD4): outputs first, then checks, failures and repairs;
  // inputs, the release review and restoration are context; raw records and attempts sit under Troubleshooting.
  function showTrace(trace) {
    const restored=restoreDetail(trace), extra=[];
    if(trace.issues?.length)extra.push(issues(trace.issues));
    if(restored)extra.push(restoreView(restored));
    if(trace.release_review) {
      const review=trace.release_review;
      extra.push(h("div",{...test("release-record")},h("h3",{text:"Release review"}),h("pre",{text:review.payload,...test("review-payload")}),
        copyControl("wf-copy-review",()=>review.payload,null),
        trace.release_decision?h("p",{...test("release-decision"),"data-decision":trace.release_decision.decision,text:"Decision: "+trace.release_decision.decision+" · "+trace.release_decision.timestamp}):null,
        h("p",{text:"Identifier check: "+review.identifier_check.status+" · "+review.identifier_check.hits.length+" matches"}),
        h("p",{text:"Local AI assessment: "+review.assessment.status}),
        review.assessment.findings.map(item=>h("p",{text:item.concern+" · "+item.location+" · "+item.explanation}))));
      if(trace.assessment_trace)extra.push(h("details",{},h("summary",{text:"Local assessment trace"}),showTrace(trace.assessment_trace)));
    }
    for(const [key,value] of entries(trace.inputs)) {
      const text=value?.text;
      if(typeof text==="string")extra.push(h("details",{},h("summary",{text:"Input · "+key}),h("pre",{text}),copyControl("wf-copy-input",()=>text,null)));
    }
    const checks=[], failures=[], attempts=[];
    for(const step of trace.steps||[]) {
      for(const [key,row] of entries(step.output_checks))checks.push({label:key,overall:row.overall||"",detail:""});
      if(step.failure)failures.push(step.kind+" · "+step.failure);
      if(step.repairs_used)failures.push(step.kind+" · repairs used: "+step.repairs_used);
      for(const id of step.attempts||[])attempts.push({id,load:async()=>{const item=await api("/api/attempts/"+encodeURIComponent(id));return item.attempt||item.record||item;}});
    }
    for(const row of trace.check_results||trace.expectation?.assertions||[])if(row&&row.operation)checks.push({label:(row.output||"")+" "+row.operation,overall:row.passed===undefined?"":row.passed?"pass":"fail"});
    const result={outcome:trace.outcome,elapsed_ms:trace.elapsed_ms,redacted:!!trace.redacted,
      outputs:entries(trace.outputs).map(([name,row])=>({name,text:row.text,format:row.format,local_only:row.local_only})),
      checks,failures,extra,troubleshooting:{raw:JSON.stringify(trace,null,2),attempts}};
    if(typeof ResultView==="function")return ResultView(result,{test:"wf-result"});
    return h("div",{},...extra,...result.outputs.map(row=>h("pre",{text:row.text})));
  }
  // Node detail opens in the detail pane, not a nested dialog; the previous node's scroll position is remembered.
  let detailKey=null; const detailScroll=new Map();
  async function inspectNode(key) {
    const current=job, epoch=generation;
    const trace=await wsApi("workflow-jobs/"+encodeURIComponent(current.id)+"?node="+encodeURIComponent(key));
    if(epoch!==generation||current!==job)return;
    const pane=main.querySelector('[data-test="wf-node-detail"]');
    if(!pane)return;
    if(detailKey!==null)detailScroll.set(detailKey,pane.scrollTop);
    detailKey=key;pane.dataset.node=key;
    replace(pane,h("h3",{text:"Node · "+key}),showTrace(trace),button("Close detail",()=>{detailKey=null;delete pane.dataset.node;replace(pane);},"close-detail"));
    pane.scrollTop=detailScroll.get(key)||0;
  }
  const PHASES={running:"Running",assessing_release:"Assessing release (local)",awaiting_release:"Awaiting your review",deciding_release:"Submitting decision"};
  const OUTCOMES={complete:"Complete",release_rejected:"Release rejected",release_expired:"Review expired",stopped:"Stopped",failed:"Failed",rejected:"Not started",unknown:"Outcome unknown",running:"Running",awaiting_release:"Awaiting your review",not_run:"Not run",dispatch_pending:"Dispatch pending"};
  // Progress in planned execution order; nodes missing from the live rows are pending.
  function nodeRows(nodes,index) {
    const order=plan?.descriptor?.order||Object.keys(nodes||{});
    const ids=[...order.filter(id=>Object.hasOwn(nodes||{},id)||job?.running),...Object.keys(nodes||{}).filter(id=>!order.includes(id))];
    return h("ol",{class:"wf-progress",...test("progress")},ids.map(id=>[id,(nodes||{})[id]||{outcome:"pending"}]).map(([id,row])=>h("li",{...test("progress-node"),"data-node":id,"data-outcome":row.outcome,text:id+" · "+row.outcome+(row.reason?" · "+row.reason:"")+
      (row.elapsed_ms!==undefined?" · "+row.elapsed_ms+" ms":"")+" "},
      row.detail_available?button("Inspect "+id,()=>inspectNode(index===undefined?id:index+":"+id),"inspect-node"):
        row.outcome!=="not_run"&&row.outcome!=="dispatch_pending"&&row.outcome!=="pending"?h("span",{text:"Trace omitted from session preview."}):null)));
  }
  function renderTrace() {
    const area=main.querySelector('[data-test="wf-trace"]');
    if(!area||!job)return;
    const result=job.result||{outcome:"running"};
    // Pinned run status: Stop, the current phase and saving state stay at the top of the setup column (AB8.2).
    const pinned=main.querySelector('[data-test="wf-run-status"]');
    if(pinned){
      const phase=job.running?(PHASES[result.phase]||"Running"):(OUTCOMES[result.outcome]||result.outcome);
      pinned.dataset.phase=job.running?(result.phase||"running"):result.outcome;
      replace(pinned,job.running?button("Stop",()=>wsApi("workflow-jobs/"+encodeURIComponent(job.id)+"/stop",{}),"stop"):null,
        h("strong",{text:phase}),h("span",{class:"ws-hint",text:result.save_error?" · Save failed":plan?.descriptor?.saving?" · Saving run evidence":" · Session only"}));
    }
    replace(area,h("h3",{text:job.name+" · "+result.outcome}), h("p",{text:"Run "+job.id+" · "+(job.running?(PHASES[result.phase]||"Running"):(OUTCOMES[result.outcome]||result.outcome))}),
      job.running?h("p",{text:"This run occupies the shared Workshop slot. Test, Compare and assistance become available when it finishes."}):null,
      result.release?releasePanel(result.release):result.phase==="assessing_release"?h("p",{text:"Assessing release locally…"}):null,
      job.batch?null:outputRows(result.outputs,result.outcome),job.batch?null:reviseNotice(result),
      issues(result.issues),result.save_error?h("p",{text:result.save_error}):null,nodeRows(result.nodes,job.batch?result.example_index:undefined),
      result.metrics?h("p",{text:`Executed ${result.metrics.executed}/${result.metrics.selected} · Complete ${result.metrics.complete}/${result.metrics.selected} · Checks passed ${result.metrics.checks_passed}/${result.metrics.selected} · Correct ${result.metrics.correct} · Incorrect ${result.metrics.incorrect} · Unknown ${result.metrics.unknown}`}):null,
      result.examples?.map((row,index)=>h("details",{},h("summary",{text:(examples.find(e=>e.id===row.id)?.label||row.id)+" · "+row.outcome+" · "+row.correctness}),
        outputRows(row.outputs,row.outcome,index),reviseNotice(row),
        row.reason?h("p",{text:row.reason}):null,
        issues(row.issues),nodeRows(row.nodes,index),
        result.reviews?.filter(review=>review.example===row.id).map(review=>h("p",{text:"Human review: "+review.judgement+(review.note?" · "+review.note:"")})),
        row.outputs_omitted?h("p",{text:"Outputs exceed the session preview limit."}):null,
        row.outcome==="complete"?button("Review correctness",()=>reviewExample(row,result),"human-review"):null)));
    if(!job.running&&plan&&!dirty&&!plan.descriptor.issues.some(row=>row.severity!=="warning"))main.querySelector('[data-test="wf-run"]')?.removeAttribute("disabled");
  }
  // A rejected or expired Gate ends that run's processing; offer Revise without a decision (CD3.6).
  function reviseNotice(result) {
    if(!["release_rejected","release_expired"].includes(result?.outcome))return null;
    const gateId=Object.keys(result.nodes||{}).find(id=>["release_rejected","release_expired"].includes(result.nodes[id]?.outcome))||
      Object.keys(draft?.nodes||{}).find(id=>draft.nodes[id].gate);
    return h("div",{class:"wf-revise",...test("revise-notice"),"data-outcome":result.outcome},
      h("p",{text:(result.outcome==="release_rejected"?"The release was rejected":"The review expired")+": processing after the Gate didn't happen."}),
      gateId&&!job?.running?button("Revise",()=>reviseFrom(gateId),"revise"):null);
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
    // Setup reads in action order: inputs → Check readiness → findings → Run; progress and detail sit beside it.
    const setup=h("section",{class:"wf-run-setup","aria-label":"Run setup"},h("div",{class:"wf-run-status",...test("run-status")}),
      options,button("Check readiness",()=>exclusive(preflight),"preflight",dirty||!token||options.disabled),
      h("section",{...test("review"),tabindex:"-1"},plan?reviewView():null),
      button(pendingStart?"Retry start":"Run",()=>exclusive(start),"run",!pendingStart&&
        (!plan||plan.descriptor.issues.some(row=>row.severity!=="warning")||!!job?.running)));
    const results=h("section",{class:"wf-run-results","aria-label":"Progress and detail"},h("section",{...test("trace")}),
      h("section",{class:"wf-node-detail",...test("node-detail")}));
    area.append(h("div",{class:"wf-run-layout",...test("run-layout")},setup,results));
    return area;
  }
  function render() {
    contractEditors=[];
    if(main.clientWidth)band=bandFor(main.clientWidth);
    const active=!!(job?.running||pendingStart);
    const toolbar=h("div",{class:"row"},select("Saved workflow",[["","Choose a workflow"],...names.map(name=>[name,name])],token?draft?.name:"",open,"picker"),
      button("New",()=>open("",true),"new",active),button("Save",()=>exclusive(save),"save",!draft||!dirty||active));
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
  // Trusted destinations and execution preferences (UI-AB v2 AB7.1). Candidates come from configuration (roles and
  // observed MCP tools) plus any the caller supplies, such as a workflow's reviewed nodes; uses are merged per exact
  // identity. Grant is offered only where it can help; ineligible rows show the configuration remedy instead.
  async function settings(candidates=[], context="") {
    const content=h("div",{class:"wf-settings"}), msg=h("p",{role:"status",...test("settings-status")}), body=h("div");
    content.append(body,msg);
    const label = (item) => [item.kind,item.url,item.server,item.tool,item.model,item.provider].filter(Boolean).join(" · ");
    function exactDestination(item) {
      const {kind,url}=item;
      return kind==="mcp"?{kind,url,server:item.server,tool:item.tool}:{kind,url,model:item.model,provider:item.provider||""};
    }
    async function reviewTrust(destination, trusted) {
      const preview=await wsApi("destination-trust-preview",{destination});
      if(trusted&&preview.reason!=="destination_untrusted"&&preview.reason!==null){
        msg.dataset.state="refused";msg.dataset.reason=preview.detail||preview.reason;
        msg.textContent=wsRemedyText(preview.detail)||"This destination cannot receive local-only data.";
        return;
      }
      const box=h("div",{},h("p",{text:label(preview.destination||destination)}),
        trusted?h("p",{class:"ws-hint",text:"A trusted service could forward data elsewhere. Trust only services you control."}):null);
      if(await dialog(trusted?"Grant destination trust":"Revoke destination trust",box,["Confirm","Cancel"])!=="Confirm")return;
      await wsApi("destination-trust",{destination:preview.destination,expected_revision:preview.revision,trusted});
      invalidatePlan();
      await loadTable();
      msg.dataset.state=trusted?"granted":"revoked";delete msg.dataset.reason;
      msg.textContent=trusted?"Destination trusted.":"Destination trust revoked.";
    }
    const table=h("section",{...test("trust-table")}),banner=h("div",{...test("saved-trust-gaps"),"data-count":"0"});
    body.append(h("h3",{text:"Trusted destinations"}),h("p",{class:"ws-hint",text:"Trust applies only to this workspace and exact destination."}),banner,table);
    async function loadTable() {
      const [data,known]=await Promise.all([wsApi("destination-trust"),wsApi("destination-trust-candidates").catch(()=>({candidates:[]}))]);
      const rows=new Map(), key=item=>JSON.stringify(exactDestination(item));
      for(const row of known.candidates){const d=row.destination;rows.set(row.exact?key(d):JSON.stringify(d),{destination:d,exact:row.exact,locality:row.locality,eligible:row.eligible,reason:row.reason,trusted:row.trusted,requires:!!row.requires_trust,remedy:row.remedy,uses:row.used_by.map(u=>u.kind+" "+u.name)});}
      // Saved-use trust gaps (core v3 §3.3): saved workflows only; the open draft is not inspected here.
      const gaps=known.trust_gaps||[],scan=known.scan||{complete:true};
      banner.replaceChildren();banner.dataset.count=String(gaps.length);banner.dataset.complete=String(scan.complete!==false);
      if(gaps.length)banner.append(h("p",{class:"warn-text",text:gaps.length+" destination"+(gaps.length===1?"":"s")+" used by saved workflows with local-only data "+(gaps.length===1?"is":"are")+" not trusted: "+gaps.map(g=>label(g.destination)).join("; ")}));
      if(scan.complete===false)banner.append(h("p",{class:"ws-hint",text:"Saved workflows could not all be checked, so this list may be incomplete."}));
      // Open-draft gaps (CD1.9): only rows whose server remedy is Grant; other remedies stay in the canvas issue list.
      const open=draft?destinationRemedies().filter(({row})=>row.remedy.action==="grant"&&row.remedy.target):[];
      banner.dataset.openCount=String(open.length);
      for(const {node,row} of open)banner.append(h("p",{class:"warn-text",...test("open-draft-gap"),"data-node":node},
        (draft.name||"New workflow")+" · open workflow"+(dirty?" (unsaved)":"")+" · node "+node+" · "+label(row.destination)+" is not trusted. ",
        button("Grant",async()=>{if(await wsGrantTrust(trustTarget(row.remedy.target))){invalidatePlan();bumpValidation();await validateNow();await loadTable();}},"open-draft-grant")));
      for(const c of candidates){
        const d=exactDestination(c),k=key(d),use=context?(context+" · node "+(c.node||"")):"this run";
        const row=rows.get(k);if(row){if(!row.uses.includes(use))row.uses.push(use);continue;}
        const preview=await wsApi("destination-trust-preview",{destination:d}).catch(()=>null);
        rows.set(k,{destination:preview?.destination||d,exact:!!preview?.destination,locality:preview?.locality,eligible:!!preview&&(preview.reason===null||preview.reason==="destination_untrusted"),reason:preview?.detail,trusted:!!preview?.trusted,uses:[use]});
      }
      for(const g of data.grants){const k=key(g);if(!rows.has(k))rows.set(k,{destination:g,exact:true,eligible:true,trusted:true,uses:[]});}
      const list=[...rows.values()].sort((a,b)=>(b.uses.length?1:0)-(a.uses.length?1:0));
      const grid=h("table",{class:"wf-trust-table"},h("thead",{},h("tr",{},...["Destination","Connects to","Local-only data","Trust","Used by",""].map(t=>h("th",{scope:"col",text:t})))));
      const tbody=h("tbody");grid.append(tbody);
      for(const row of list){
        const d=row.destination,action=h("td");
        if(row.trusted)action.append(button("Revoke",()=>reviewTrust(exactDestination(d),false),"revoke-trust"));
        else if(row.eligible&&row.exact)action.append(button("Grant",()=>reviewTrust(exactDestination(d),true),"candidate-trust"));
        else if(!row.eligible)action.append(h("span",{class:"ws-hint",...test("trust-remedy"),"data-action":row.remedy?.action||"",text:wsRemedyText(row.reason)||"Not eligible."}));
        tbody.append(h("tr",{...test("trust-row"),"data-eligible":String(row.eligible),"data-trusted":String(row.trusted),"data-used":String(row.uses.length>0),"data-requires-trust":String(!!row.requires)},
          h("td",{text:label(d)}),h("td",{text:row.locality==="local"?"Local endpoint":row.locality?"Remote endpoint":"—"}),
          h("td",{text:row.eligible?"Eligible":"Not eligible"}),h("td",{text:row.trusted?"Trusted":"Not trusted"}),
          h("td",{text:row.uses.length?row.uses.join(", "):"No known uses"}),action));
      }
      table.replaceChildren(list.length?grid:h("p",{text:"No destinations are configured yet."}));
    }
    await loadTable();
    // Manual entry, folded: exact retyping is a fallback for destinations not configured here.
    const fields=h("details",{...test("trust-manual")},h("summary",{text:"Add a destination by hand"}));
    const destination={kind:"lmstudio",url:"",model:"",provider:"",server:"",tool:""};
    const names=h("div"), live=h("p",{class:"ws-hint",...test("trust-manual-status")});
    let liveGeneration=0;
    async function check(){const mine=++liveGeneration;if(!destination.url){live.textContent="";return;}
      const p=await wsApi("destination-trust-preview",{destination:exactDestination(destination)}).catch(()=>null);if(mine!==liveGeneration||!p)return;
      live.dataset.reason=p.detail||p.reason||"";live.textContent=p.reason===null||p.reason==="destination_untrusted"?(p.trusted?"Already trusted.":"Eligible for local-only data."):(wsRemedyText(p.detail)||"Not eligible.");}
    function drawFields(){
      const keys=destination.kind==="mcp"?[["server","Server"],["tool","Tool"]]:[["model","Model"],["provider","Provider (optional)"]];
      replace(names,keys.map(([key,text])=>field(text,destination[key],v=>{destination[key]=v;check();})));
    }
    fields.append(select("Kind",[["lmstudio","LM Studio"],["openai_compat","OpenAI compatible"],["mcp","MCP tool"]],destination.kind,v=>{
      destination.kind=v;drawFields();check();
    }),field("Endpoint URL","",v=>{destination.url=v;check();}),names,live,button("Review destination",()=>reviewTrust(exactDestination(destination),true),"review-trust"));
    drawFields();body.append(fields);
    // Execution preferences are a separate concern from trust.
    const pref=await wsApi("workflow-mcp-confirmation");
    let mode=pref.mode;
    body.append(h("h3",{text:"Execution preferences"}),select("Workflow MCP confirmation",[["every_run","Every MCP workflow run"],["effectful_batches","Only MCP batches"]],mode,v=>mode=v,"mcp-confirmation"),
      pref.invalid?h("p",{text:"Invalid saved setting. Using Every MCP workflow run."}):null,
      button("Save confirmation preference",async()=>{
        const result=await wsApi("workflow-mcp-confirmation",{mode,expected_revision:pref.revision});
        Object.assign(pref,result);invalidatePlan();msg.dataset.state="preference";msg.textContent="Confirmation preference saved.";
      },"save-mcp-confirmation"));
    await dialog("Trusted destinations",content,["Close"]);
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
  // Content-width bands (§0.1): the palette becomes a rail whenever the three pane minimums don't fit.
  if(typeof ResizeObserver==="function")new ResizeObserver(()=>{
    if(!main.clientWidth)return;
    const next=bandFor(main.clientWidth);
    if(next!==band){band=next;if(draft&&view==="design")renderDesignSurface();}
  }).observe(main);
  render();
  // Return from a detour (CD1.13): an unsaved draft, its selection, viewport, undo history, run inputs and
  // examples are kept in memory as they were; only the palette's block list is refreshed. A saved workflow is
  // reloaded and, after Open source revision, the explicit Rebind review opens for the affected node.
  async function returnTo(info = {}) {
    const target=window.agentwizWorkflowReturn;window.agentwizWorkflowReturn=null;
    if(!target)return;
    palette.blocks=null;palette.details=new Map();
    if(info.key)palette.highlight=info.key;
    const rebindText=target.revise?"A revised block needs explicit Rebind and Save, then Check readiness, a new run and a fresh Gate decision.":
      "Nothing is rebound until you confirm and save.";
    const reviewNode=()=>target.node&&draft&&Object.hasOwn(draft.nodes,target.node)&&!draft.nodes[target.node].gate&&
      draft.nodes[target.node].snapshot_id&&!rebind&&!Object.keys(selections).length;
    if(draft&&(dirty||!target.workflow)&&(draft.name||"")===(target.workflow||"")) {
      view="design";invalidatePlan();vgen++;render();
      report(target.node?"Review the replacement revision for node "+target.node+". "+rebindText:info.key?"Block "+info.key+" is in the palette.":"");
      if(reviewNode())await pickSource(target.node);
      return;
    }
    if(!target.workflow){render();return;}
    await refresh();
    load(await wsApi(route(target.workflow)),{inputs:target.inputs||{},selected:target.selected||[],selection,viewport,view:"design"});
    report(target.node?"Review the replacement revision for node "+target.node+". "+rebindText:info.key?"Block "+info.key+" is in the palette.":"");
    if(reviewNode())await pickSource(target.node);
  }
  function showRun(){if(!draft)return;view="run-view";render();}
  return {onShow:()=>exclusive(async()=>{palette.blocks=null;await refresh();await recoverActive();render();}).catch(error), showRun, leave, settings, evidence, returnTo, traceView:showTrace,
    tutorial:async(name,text,example,port="text")=>{
      await refresh();load(await wsApi(route(name)));view="run-view";inputs[port]=text;
      if(example)selected.add(example);render();
    }
  };
};

