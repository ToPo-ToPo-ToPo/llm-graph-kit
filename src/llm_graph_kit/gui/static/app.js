/*
 * llm-graph-kit GUI frontend
 *
 * - Drawflow を使ってノード/エッジを GUI で組み立てる
 * - バックエンドの /api/run, /api/codegen, /api/mermaid を呼び出す
 *
 * 設計メモ:
 *   ノードは Drawflow の data 経由でメタデータを保持する。
 *     - START / END: data.kind == "start" | "end"
 *     - function:    data = { kind, name, code }
 *     - conditional: data = { kind, name, condition_kind, condition_value, signals: [] }
 *   conditional は出力ポート N 個を持ち、signals[i] が output_{i+1} のラベルになる。
 */

// ============================================================
// 状態
// ============================================================
let editor = null;
let startId = null;
let endId = null;
let stateFields = [];   // [{name, type}]
let selectedNodeId = null;
let suppressRemove = false;

// ============================================================
// 起動
// ============================================================
window.addEventListener("DOMContentLoaded", init);

function init() {
  const container = document.getElementById("drawflow");
  editor = new Drawflow(container);
  editor.reroute = true;
  editor.reroute_fix_curvature = true;
  editor.editor_mode = "edit";
  editor.start();

  editor.on("nodeSelected", id => onNodeSelected(parseInt(id, 10)));
  editor.on("nodeUnselected", () => onNodeUnselected());
  editor.on("nodeRemoved", id => onNodeRemoved(parseInt(id, 10)));
  editor.on("nodeMoved", id => {/* no-op */});

  // toolbar
  document.getElementById("btn-add-function").onclick = () => addFunctionNode();
  document.getElementById("btn-add-conditional").onclick = () => addConditionalNode();
  document.getElementById("btn-run").onclick = runGraph;
  document.getElementById("btn-mermaid").onclick = showMermaid;
  document.getElementById("btn-codegen").onclick = showCodegen;
  document.getElementById("btn-save").onclick = saveGraph;
  document.getElementById("btn-load").onclick = () => document.getElementById("file-input").click();
  document.getElementById("btn-add-field").onclick = () => { stateFields.push({name: "", type: "str"}); renderStateFields(); };
  document.getElementById("btn-clear-log").onclick = clearLog;
  document.getElementById("file-input").addEventListener("change", onFilePicked);
  document.getElementById("modal-close").onclick = () => document.getElementById("modal").close();
  document.getElementById("modal-copy").onclick = copyModal;

  // initial demo
  stateFields = [
    {name: "input", type: "str"},
    {name: "output", type: "str"},
  ];
  renderStateFields();

  startId = createStartNode(60, 200);
  endId = createEndNode(900, 200);

  // demo: a single function node
  const demoId = addFunctionNode({
    name: "echo",
    code: 'def echo(state):\n    yield {"type": "log", "node": "echo", "content": "echoing..."}\n    return {"output": state.get("input", "")}',
    x: 420, y: 180,
  });
  // connect START -> echo -> END
  connect(startId, "output_1", demoId, "input_1");
  connect(demoId, "output_1", endId, "input_1");
}

// ============================================================
// ノード生成
// ============================================================
function createStartNode(x, y) {
  const html = `
    <div class="node-content">
      <div class="node-header kind-start">START</div>
      <div class="node-body">エントリーポイント</div>
    </div>
  `;
  return editor.addNode("start", 0, 1, x, y, "kind-start", { kind: "start" }, html);
}

function createEndNode(x, y) {
  const html = `
    <div class="node-content">
      <div class="node-header kind-end">END</div>
      <div class="node-body">終端</div>
    </div>
  `;
  return editor.addNode("end", 1, 0, x, y, "kind-end", { kind: "end" }, html);
}

function addFunctionNode(opts = {}) {
  const name = opts.name || uniqueName("node");
  const code = opts.code != null ? opts.code : defaultFunctionCode(name);
  const data = { kind: "function", name, code };
  const html = renderFunctionHTML(data);
  const x = opts.x != null ? opts.x : 350 + Math.random() * 100;
  const y = opts.y != null ? opts.y : 100 + Math.random() * 200;
  const id = editor.addNode("function", 1, 1, x, y, "kind-function", data, html);
  return id;
}

function addConditionalNode(opts = {}) {
  const name = opts.name || uniqueName("check");
  const signals = opts.signals || ["ok", "retry"];
  const data = {
    kind: "conditional",
    name,
    condition_kind: opts.condition_kind || "key",
    condition_value: opts.condition_value || "decision",
    signals: signals.slice(),
    code: opts.code || "",
  };
  const html = renderConditionalHTML(data);
  const x = opts.x != null ? opts.x : 500 + Math.random() * 100;
  const y = opts.y != null ? opts.y : 100 + Math.random() * 200;
  const id = editor.addNode("conditional", 1, signals.length, x, y, "kind-conditional", data, html);
  return id;
}

function defaultFunctionCode(name) {
  return `def ${name}(state):
    # ここに処理を書く。dict を返すと state にマージされる
    yield {"type": "log", "node": "${name}", "content": "processing..."}
    return None
`;
}

// ============================================================
// ノード内 HTML
// ============================================================
function renderFunctionHTML(data) {
  return `
    <div class="node-content">
      <div class="node-header kind-function">FUNCTION</div>
      <div class="node-body">
        <div class="node-name">${escapeHTML(data.name)}</div>
      </div>
    </div>
  `;
}

function renderConditionalHTML(data) {
  const signalList = (data.signals || []).map(s =>
    `<div class="signal-label">→ ${escapeHTML(s)}</div>`
  ).join("");
  return `
    <div class="node-content">
      <div class="node-header kind-conditional">CONDITIONAL</div>
      <div class="node-body">
        <div class="node-name">${escapeHTML(data.name)}</div>
        <div class="signals">${signalList}</div>
      </div>
    </div>
  `;
}

function refreshNodeDisplay(id) {
  const node = getNode(id);
  if (!node) return;
  const data = node.data;
  let html;
  if (data.kind === "function") html = renderFunctionHTML(data);
  else if (data.kind === "conditional") html = renderConditionalHTML(data);
  else return;

  const el = document.querySelector(`#node-${id} .drawflow_content_node`);
  if (el) el.innerHTML = html;
}

// ============================================================
// 接続
// ============================================================
function connect(srcId, outputClass, dstId, inputClass) {
  try {
    editor.addConnection(srcId, dstId, outputClass, inputClass);
  } catch (e) {
    console.warn("addConnection failed", e);
  }
}

// ============================================================
// 選択 / プロパティパネル
// ============================================================
function onNodeSelected(id) {
  selectedNodeId = id;
  renderProperties(id);
}

function onNodeUnselected() {
  selectedNodeId = null;
  renderEmptyProperties();
}

function onNodeRemoved(id) {
  if (suppressRemove) return;
  if (id === startId) {
    log("warn", "START が消されたので再生成しました。");
    startId = createStartNode(60, 200);
  } else if (id === endId) {
    log("warn", "END が消されたので再生成しました。");
    endId = createEndNode(900, 200);
  }
  if (selectedNodeId === id) onNodeUnselected();
}

function renderEmptyProperties() {
  document.getElementById("node-properties").innerHTML =
    '<p class="hint">ノードをクリックすると編集できます。</p>';
}

function renderProperties(id) {
  const node = getNode(id);
  if (!node) { renderEmptyProperties(); return; }
  const data = node.data;
  const container = document.getElementById("node-properties");

  if (data.kind === "start" || data.kind === "end") {
    container.innerHTML = `
      <div class="prop-row">
        <label>Kind</label>
        <div>${data.kind.toUpperCase()} (固定ノード)</div>
      </div>
      <p class="hint">START / END は削除しても自動で再生成されます。</p>
    `;
    return;
  }

  if (data.kind === "function") {
    container.innerHTML = `
      <div class="prop-row">
        <label>Kind</label>
        <div>FUNCTION</div>
      </div>
      <div class="prop-row">
        <label>Name</label>
        <input id="prop-name" type="text" value="${escapeAttr(data.name)}" />
      </div>
      <div class="prop-row">
        <label>Code (Python function)</label>
        <textarea id="prop-code" rows="14" spellcheck="false">${escapeHTML(data.code)}</textarea>
      </div>
      <div class="prop-row">
        <button class="btn btn-danger btn-small" id="prop-delete">Delete Node</button>
      </div>
    `;
    document.getElementById("prop-name").oninput = () => updateFunctionData(id);
    document.getElementById("prop-code").oninput = () => updateFunctionData(id);
    document.getElementById("prop-delete").onclick = () => deleteNode(id);
    return;
  }

  if (data.kind === "conditional") {
    const signalsHTML = (data.signals || []).map((s, i) => `
      <div class="signal-row">
        <input type="text" class="signal-input" data-idx="${i}" value="${escapeAttr(s)}" />
        <button class="btn btn-danger btn-small signal-del" data-idx="${i}">×</button>
      </div>
    `).join("");

    container.innerHTML = `
      <div class="prop-row">
        <label>Kind</label>
        <div>CONDITIONAL</div>
      </div>
      <div class="prop-row">
        <label>Name</label>
        <input id="prop-name" type="text" value="${escapeAttr(data.name)}" />
      </div>
      <div class="prop-row">
        <label>Condition Kind</label>
        <select id="prop-cond-kind">
          <option value="key" ${data.condition_kind === "key" ? "selected" : ""}>state[key] を使う</option>
          <option value="code" ${data.condition_kind === "code" ? "selected" : ""}>callable (lambda / def)</option>
        </select>
      </div>
      <div class="prop-row">
        <label>Condition Value</label>
        ${data.condition_kind === "code"
          ? `<textarea id="prop-cond-value" rows="4" spellcheck="false">${escapeHTML(data.condition_value)}</textarea>`
          : `<input id="prop-cond-value" type="text" value="${escapeAttr(data.condition_value)}" />`}
      </div>
      <div class="prop-row">
        <label>Optional Node Body Code</label>
        <textarea id="prop-code" rows="6" spellcheck="false" placeholder="def name(state): ... (省略可)">${escapeHTML(data.code || "")}</textarea>
        <p class="hint">未入力なら何もしない passthrough として登録されます。</p>
      </div>
      <div class="prop-row">
        <label>Outputs (signals, top→bottom)</label>
        <div id="signals-list">${signalsHTML}</div>
        <button class="btn btn-small" id="prop-add-signal">+ Output</button>
      </div>
      <div class="prop-row">
        <button class="btn btn-danger btn-small" id="prop-delete">Delete Node</button>
      </div>
    `;
    document.getElementById("prop-name").oninput = () => updateConditionalData(id);
    document.getElementById("prop-cond-kind").onchange = () => {
      updateConditionalData(id);
      renderProperties(id); // 入力欄の型が変わるので再描画
    };
    document.getElementById("prop-cond-value").oninput = () => updateConditionalData(id);
    document.getElementById("prop-code").oninput = () => updateConditionalData(id);
    document.querySelectorAll(".signal-input").forEach(el => {
      el.oninput = () => updateConditionalData(id);
    });
    document.querySelectorAll(".signal-del").forEach(el => {
      el.onclick = () => removeSignal(id, parseInt(el.dataset.idx, 10));
    });
    document.getElementById("prop-add-signal").onclick = () => addSignal(id);
    document.getElementById("prop-delete").onclick = () => deleteNode(id);
    return;
  }
}

function updateFunctionData(id) {
  const node = getNode(id);
  if (!node) return;
  node.data.name = document.getElementById("prop-name").value;
  node.data.code = document.getElementById("prop-code").value;
  editor.updateNodeDataFromId(id, node.data);
  refreshNodeDisplay(id);
}

function updateConditionalData(id) {
  const node = getNode(id);
  if (!node) return;
  node.data.name = document.getElementById("prop-name").value;
  node.data.condition_kind = document.getElementById("prop-cond-kind").value;
  node.data.condition_value = document.getElementById("prop-cond-value").value;
  const codeEl = document.getElementById("prop-code");
  node.data.code = codeEl ? codeEl.value : "";

  const inputs = document.querySelectorAll(".signal-input");
  const newSignals = [];
  inputs.forEach(el => newSignals.push(el.value));
  node.data.signals = newSignals;
  editor.updateNodeDataFromId(id, node.data);
  refreshNodeDisplay(id);
}

function addSignal(id) {
  const node = getNode(id);
  if (!node) return;
  node.data.signals.push("new_signal");
  editor.updateNodeDataFromId(id, node.data);
  editor.addNodeOutput(id);
  refreshNodeDisplay(id);
  renderProperties(id);
}

function removeSignal(id, idx) {
  const node = getNode(id);
  if (!node) return;
  if (node.data.signals.length <= 1) {
    log("warn", "最低 1 つの output は必要です。");
    return;
  }
  // Drawflow のポートクラスは output_1, output_2, ... で連続している
  const outputClass = `output_${idx + 1}`;
  editor.removeNodeOutput(id, outputClass);
  node.data.signals.splice(idx, 1);
  editor.updateNodeDataFromId(id, node.data);
  refreshNodeDisplay(id);
  renderProperties(id);
}

function deleteNode(id) {
  if (id === startId || id === endId) {
    log("warn", "START / END は削除できません。");
    return;
  }
  editor.removeNodeId(`node-${id}`);
  onNodeUnselected();
}

// ============================================================
// state schema 編集
// ============================================================
function renderStateFields() {
  const wrap = document.getElementById("state-fields");
  wrap.innerHTML = stateFields.map((f, i) => `
    <div class="field-row">
      <input type="text" data-i="${i}" data-k="name" value="${escapeAttr(f.name)}" placeholder="field name" />
      <select data-i="${i}" data-k="type">
        ${["str","int","float","bool","list","dict","Any"].map(t =>
          `<option value="${t}" ${f.type === t ? "selected" : ""}>${t}</option>`
        ).join("")}
      </select>
      <button class="btn btn-danger btn-small" data-del="${i}">×</button>
    </div>
  `).join("");
  wrap.querySelectorAll("input, select").forEach(el => {
    el.addEventListener("input", e => {
      const i = parseInt(el.dataset.i, 10);
      const k = el.dataset.k;
      stateFields[i][k] = el.value;
    });
  });
  wrap.querySelectorAll("[data-del]").forEach(el => {
    el.addEventListener("click", () => {
      stateFields.splice(parseInt(el.dataset.del, 10), 1);
      renderStateFields();
    });
  });
}

// ============================================================
// グラフ仕様の入出力
// ============================================================
function getNode(id) {
  try {
    return editor.getNodeFromId(id);
  } catch {
    return null;
  }
}

function exportSpec() {
  const dfData = editor.drawflow.drawflow.Home.data;
  const idToSpec = {};
  const nodes = [];
  const edges = [];

  for (const [idStr, node] of Object.entries(dfData)) {
    const id = idStr; // 文字列のまま
    const data = node.data || {};
    const kind = data.kind;
    let frontendId;
    if (kind === "start") { frontendId = "__start__"; }
    else if (kind === "end") { frontendId = "__end__"; }
    else { frontendId = `n${id}`; }

    idToSpec[id] = frontendId;

    if (kind === "function") {
      nodes.push({
        id: frontendId,
        kind: "function",
        name: data.name,
        code: data.code,
        position: { x: node.pos_x, y: node.pos_y },
      });
    } else if (kind === "conditional") {
      nodes.push({
        id: frontendId,
        kind: "conditional",
        name: data.name,
        code: data.code || "",
        condition_kind: data.condition_kind,
        condition_value: data.condition_value,
        signals: data.signals,
        position: { x: node.pos_x, y: node.pos_y },
      });
    } else if (kind === "start") {
      nodes.push({ id: "__start__", kind: "start", position: { x: node.pos_x, y: node.pos_y } });
    } else if (kind === "end") {
      nodes.push({ id: "__end__", kind: "end", position: { x: node.pos_x, y: node.pos_y } });
    }
  }

  // edges from outputs
  for (const [idStr, node] of Object.entries(dfData)) {
    const data = node.data || {};
    if (!node.outputs) continue;
    for (const [outClass, port] of Object.entries(node.outputs)) {
      const conns = port.connections || [];
      for (const c of conns) {
        const targetId = String(c.node);
        const srcSpecId = idToSpec[idStr];
        const dstSpecId = idToSpec[targetId];
        if (!srcSpecId || !dstSpecId) continue;
        let signal = null;
        if (data.kind === "conditional") {
          // output_X の X を 1-indexed で取って signals に対応付ける
          const idx = parseInt(outClass.split("_")[1], 10) - 1;
          signal = (data.signals || [])[idx] || null;
        }
        edges.push({
          source: srcSpecId,
          target: dstSpecId,
          signal: signal,
        });
      }
    }
  }

  let initialState = {};
  try {
    const raw = document.getElementById("initial-state").value.trim();
    if (raw) initialState = JSON.parse(raw);
  } catch (e) {
    throw new Error(`initial_state の JSON パースに失敗: ${e.message}`);
  }

  return {
    state_schema: stateFields.filter(f => f.name && f.name.trim()),
    nodes,
    edges,
    initial_state: initialState,
    max_steps: parseInt(document.getElementById("max-steps").value, 10) || 100,
  };
}

function importSpec(spec) {
  // クリア
  suppressRemove = true;
  editor.clearModuleSelected();
  suppressRemove = false;

  stateFields = (spec.state_schema || []).map(f => ({ name: f.name || "", type: f.type || "str" }));
  renderStateFields();

  document.getElementById("initial-state").value = JSON.stringify(spec.initial_state || {}, null, 2);
  document.getElementById("max-steps").value = spec.max_steps || 100;

  // 仕様 id -> drawflow numeric id のマップ
  const idMap = {};
  for (const n of (spec.nodes || [])) {
    const pos = n.position || {};
    const x = pos.x != null ? pos.x : 200 + Math.random() * 400;
    const y = pos.y != null ? pos.y : 100 + Math.random() * 300;
    if (n.kind === "start") {
      idMap[n.id] = startId = createStartNode(x, y);
    } else if (n.kind === "end") {
      idMap[n.id] = endId = createEndNode(x, y);
    } else if (n.kind === "function") {
      idMap[n.id] = addFunctionNode({ name: n.name, code: n.code, x, y });
    } else if (n.kind === "conditional") {
      idMap[n.id] = addConditionalNode({
        name: n.name,
        condition_kind: n.condition_kind,
        condition_value: n.condition_value,
        signals: n.signals || ["ok"],
        code: n.code || "",
        x, y,
      });
    }
  }

  // START/END が spec に無ければ確保
  if (!Object.values(spec.nodes || []).some(n => n.kind === "start")) {
    startId = createStartNode(60, 200);
    idMap["__start__"] = startId;
  }
  if (!Object.values(spec.nodes || []).some(n => n.kind === "end")) {
    endId = createEndNode(900, 200);
    idMap["__end__"] = endId;
  }

  // エッジ接続
  for (const e of (spec.edges || [])) {
    const srcDf = idMap[e.source];
    const dstDf = idMap[e.target];
    if (srcDf == null || dstDf == null) continue;
    let outClass = "output_1";
    // conditional の signal で port index を逆引き
    const srcNodeSpec = spec.nodes.find(n => n.id === e.source);
    if (srcNodeSpec && srcNodeSpec.kind === "conditional" && e.signal) {
      const idx = (srcNodeSpec.signals || []).indexOf(e.signal);
      if (idx >= 0) outClass = `output_${idx + 1}`;
    }
    connect(srcDf, outClass, dstDf, "input_1");
  }
}

// ============================================================
// 保存 / 読み込み
// ============================================================
function saveGraph() {
  let spec;
  try { spec = exportSpec(); }
  catch (e) { log("error", e.message); return; }
  const blob = new Blob([JSON.stringify(spec, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "graph.json";
  a.click();
  URL.revokeObjectURL(url);
  log("log", "保存しました: graph.json");
}

function onFilePicked(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    try {
      const spec = JSON.parse(ev.target.result);
      importSpec(spec);
      log("log", `読み込み: ${file.name}`);
    } catch (err) {
      log("error", `読み込み失敗: ${err.message}`);
    }
  };
  reader.readAsText(file);
  e.target.value = ""; // 同じファイルを再選択できるように
}

// ============================================================
// 実行 / コード生成 / Mermaid
// ============================================================
async function runGraph() {
  setStatus("running");
  clearLog();
  let spec;
  try { spec = exportSpec(); }
  catch (e) { log("error", e.message); setStatus("error"); return; }

  log("log", "POST /api/run...");

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    });
    if (!res.body) throw new Error("response body is empty");

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (chunk.startsWith("data: ")) {
          const json = chunk.slice(6).trim();
          try {
            const evt = JSON.parse(json);
            handleEvent(evt);
          } catch (e) {
            log("error", `SSE parse failed: ${e.message}`);
          }
        }
      }
    }
    setStatus("done");
  } catch (e) {
    log("error", `実行エラー: ${e.message}`);
    setStatus("error");
  }
}

function handleEvent(evt) {
  const type = evt.type || "log";
  if (type === "done") return;
  if (type === "final") {
    log("final", `final state: ${JSON.stringify(evt.state, null, 2)}`);
    return;
  }
  if (type === "fatal") {
    log("fatal", `${evt.content}\n${evt.traceback || ""}`);
    return;
  }
  if (type === "answer_text") {
    appendInline(evt.content || "");
    return;
  }
  let label = type;
  if (evt.node || evt.agent) label = `${type} [${evt.node || evt.agent}]`;
  const content = evt.content != null ? evt.content : JSON.stringify(evt);
  log(type, `${label}: ${content}`);
}

async function showMermaid() {
  let spec;
  try { spec = exportSpec(); }
  catch (e) { log("error", e.message); return; }
  const res = await fetch("/api/mermaid", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });
  const data = await res.json();
  if (data.error) {
    showModal("Error", data.error + "\n\n" + (data.traceback || ""));
    return;
  }
  showModal("Mermaid", data.mermaid);
}

async function showCodegen() {
  let spec;
  try { spec = exportSpec(); }
  catch (e) { log("error", e.message); return; }
  const res = await fetch("/api/codegen", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });
  const data = await res.json();
  if (data.error) {
    showModal("Error", data.error + "\n\n" + (data.traceback || ""));
    return;
  }
  showModal("Python Code", data.code);
}

// ============================================================
// モーダル
// ============================================================
function showModal(title, body) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").textContent = body;
  document.getElementById("modal").showModal();
}

async function copyModal() {
  const text = document.getElementById("modal-body").textContent;
  try {
    await navigator.clipboard.writeText(text);
    log("log", "クリップボードにコピーしました。");
  } catch (e) {
    log("error", `コピー失敗: ${e.message}`);
  }
}

// ============================================================
// イベントログ
// ============================================================
function setStatus(s) {
  const el = document.getElementById("run-status");
  el.className = "status " + s;
  el.textContent = s;
}

function log(type, content) {
  const el = document.getElementById("log");
  const line = document.createElement("div");
  line.className = "log-line";
  const tag = document.createElement("span");
  tag.className = `tag tag-${type}`;
  tag.textContent = type;
  line.appendChild(tag);
  line.appendChild(document.createTextNode(content));
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function appendInline(text) {
  const el = document.getElementById("log");
  let last = el.lastElementChild;
  if (!last || !last.dataset.inline) {
    last = document.createElement("div");
    last.className = "log-line";
    last.dataset.inline = "1";
    const tag = document.createElement("span");
    tag.className = "tag tag-answer_text";
    tag.textContent = "answer";
    last.appendChild(tag);
    last.appendChild(document.createElement("span"));
    el.appendChild(last);
  }
  last.lastElementChild.appendChild(document.createTextNode(text));
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  document.getElementById("log").innerHTML = "";
}

// ============================================================
// helpers
// ============================================================
function uniqueName(base) {
  const used = new Set();
  const all = editor ? editor.drawflow.drawflow.Home.data : {};
  for (const n of Object.values(all)) {
    if (n.data && n.data.name) used.add(n.data.name);
  }
  let i = 1;
  while (used.has(`${base}_${i}`)) i++;
  return `${base}_${i}`;
}

function escapeHTML(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  if (s == null) return "";
  return String(s).replace(/"/g, "&quot;");
}
