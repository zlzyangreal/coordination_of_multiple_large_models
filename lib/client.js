window.__ModuleLoader__.load({
  id: "dsh-multimodel-coordination",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    var react = require("react");
    var React = react.default !== undefined ? react.default : react;
    var h = React.createElement;
    var useState = React.useState;
    var useEffect = React.useEffect;
    var useRef = React.useRef;

    var API = "/_multimodel";
    var C = {
      text: "#e6e9ef", muted: "#9aa4b2", line: "#2a3240", card: "#161b23", accent: "#4f8cff",
      ok: "#3fb950", warn: "#d29922", bad: "#f85149", chip: "#202836",
    };
    var S = {
      page: { padding: "18px 22px 40px", color: C.text, lineHeight: 1.5, boxSizing: "border-box", height: "100%", width: "100%", maxWidth: "100%", overflow: "auto" },
      h2: { fontSize: "18px", margin: "0 0 6px" },
      muted: { color: C.muted, fontSize: "12px", margin: "0 0 10px" },
      card: { background: C.card, border: "1px solid " + C.line, borderRadius: "8px", padding: "14px 16px", margin: "12px 0" },
      row: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" },
      h3: { fontSize: "14px", margin: "0 0 8px", color: C.text },
      small: { fontSize: "12px", color: C.muted },
      button: { background: C.accent, color: "#fff", border: "0", borderRadius: "6px", padding: "6px 12px", cursor: "pointer", fontSize: "13px" },
      buttonGhost: { background: "transparent", color: C.text, border: "1px solid " + C.line, borderRadius: "6px", padding: "5px 10px", cursor: "pointer", fontSize: "12px" },
      buttonDanger: { background: C.bad, color: "#fff", border: "0", borderRadius: "6px", padding: "6px 12px", cursor: "pointer", fontSize: "13px" },
      pill: { display: "inline-block", padding: "2px 8px", borderRadius: "999px", fontSize: "11px", border: "1px solid " + C.line },
      input: { accentColor: C.accent },
      code: { fontFamily: "ui-monospace,Consolas,monospace", fontSize: "12px", background: "#0d1117", border: "1px solid " + C.line, borderRadius: "6px", padding: "10px", whiteSpace: "pre-wrap", maxHeight: "260px", overflow: "auto" },
      dialog: { position: "fixed", inset: "0", background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 99 },
      dialogBox: { background: C.card, border: "1px solid " + C.line, borderRadius: "10px", padding: "18px", width: "560px", maxWidth: "92vw", color: C.text },
      table: { width: "100%", borderCollapse: "collapse", fontSize: "12px" },
      th: { textAlign: "left", padding: "4px 6px", color: C.muted, fontWeight: 500, borderBottom: "1px solid " + C.line },
      td: { padding: "4px 6px", borderBottom: "1px solid " + C.line, verticalAlign: "top" },
    };

    function pill(text, color) {
      return h("span", { style: { ...S.pill, color: color || C.text, borderColor: color || C.line } }, text);
    }

    function priorityColor(priority) {
      return priority === "I0" ? C.bad : priority === "I1" ? C.warn : priority === "I2" ? C.accent : C.muted;
    }

    function layoutDag(nodes, edges) {
      var inputNodes = Array.isArray(nodes) ? nodes : [];
      if (inputNodes.length === 0) return { width: 0, height: 0, nodes: [], edges: [] };

      var nodeWidth = 168;
      var nodeHeight = 64;
      var gap = 24;
      var padding = 16;
      var known = Object.create(null);
      var ordered = [];
      inputNodes.forEach(function (node, index) {
        if (!node || node.id === undefined || node.id === null) return;
        var id = String(node.id);
        if (known[id]) return;
        known[id] = { node: node, index: index, id: id };
        ordered.push(known[id]);
      });
      if (ordered.length === 0) return { width: 0, height: 0, nodes: [], edges: [] };

      var validEdges = (Array.isArray(edges) ? edges : []).filter(function (edge) {
        return edge && known[String(edge.from)] && known[String(edge.to)] && String(edge.from) !== String(edge.to);
      }).map(function (edge) {
        return { from: String(edge.from), to: String(edge.to), kind: edge.kind || "depends" };
      });
      var depth = Object.create(null);
      ordered.forEach(function (entry) { depth[entry.id] = 0; });
      var forwardEdges = validEdges.filter(function (edge) { return edge.kind !== "rework"; });
      for (var pass = 0; pass < ordered.length - 1; pass += 1) {
        var changed = false;
        forwardEdges.forEach(function (edge) {
          var candidate = depth[edge.from] + 1;
          if (candidate > depth[edge.to]) {
            depth[edge.to] = candidate;
            changed = true;
          }
        });
        if (!changed) break;
      }

      var columns = Object.create(null);
      ordered.forEach(function (entry) {
        var key = String(depth[entry.id]);
        if (!columns[key]) columns[key] = [];
        columns[key].push(entry);
      });
      Object.keys(columns).forEach(function (key) {
        columns[key].sort(function (left, right) {
          return left.index - right.index || left.id.localeCompare(right.id);
        });
      });

      var positioned = [];
      var positions = Object.create(null);
      var maxRight = 0;
      var maxBottom = 0;
      ordered.forEach(function (entry) {
        var sameDepth = columns[String(depth[entry.id])];
        var column = sameDepth.indexOf(entry);
        var item = {
          id: entry.id,
          x: padding + column * (nodeWidth + gap),
          y: depth[entry.id] * 96,
          w: nodeWidth,
          h: nodeHeight,
          depth: depth[entry.id],
        };
        positioned.push(item);
        positions[item.id] = item;
        maxRight = Math.max(maxRight, item.x + item.w);
        maxBottom = Math.max(maxBottom, item.y + item.h);
      });

      var renderedEdges = validEdges.map(function (edge) {
        var from = positions[edge.from];
        var to = positions[edge.to];
        var startX = from.x + from.w / 2;
        var startY = edge.kind === "rework" ? from.y : from.y + from.h;
        var endX = to.x + to.w / 2;
        var endY = edge.kind === "rework" ? to.y + to.h : to.y;
        var middleY = (startY + endY) / 2;
        return {
          from: edge.from,
          to: edge.to,
          kind: edge.kind,
          path: "M " + startX + " " + startY + " C " + startX + " " + middleY + ", " + endX + " " + middleY + ", " + endX + " " + endY,
        };
      });

      return {
        width: maxRight + padding,
        height: maxBottom + padding,
        nodes: positioned,
        edges: renderedEdges,
      };
    }

    function workflowEdges(nodes) {
      var list = Array.isArray(nodes) ? nodes : [];
      var known = Object.create(null);
      var reviewsByProducer = {};
      list.forEach(function (node) {
        if (node && node.id !== undefined && node.id !== null) known[String(node.id)] = node;
      });
      var edges = [];
      list.forEach(function (node) {
        if (!node || node.id === undefined || node.id === null) return;
        var id = String(node.id);
        (Array.isArray(node.depends_on) ? node.depends_on : []).forEach(function (dependency) {
          var from = String(dependency);
          if (known[from]) edges.push({ from: from, to: id, kind: "depends" });
        });
        if (node.kind === "review" && node.reviews !== undefined && node.reviews !== null) {
          var producer = String(node.reviews);
          if (known[producer]) {
            edges.push({ from: producer, to: id, kind: "review" });
            reviewsByProducer[producer] = id;
          }
        }
      });
      list.forEach(function (node) {
        if (!node || node.id === undefined || node.id === null || node.status !== "rework") return;
        var producer = String(node.id);
        if (reviewsByProducer[producer]) {
          edges.push({ from: reviewsByProducer[producer], to: producer, kind: "rework" });
        }
      });
      return edges;
    }

    async function apiGet(path) {
      var response = await fetch(API + path, { cache: "no-store" });
      var body = await response.json();
      if (!response.ok) throw new Error(body.message || body.error || ("HTTP " + response.status));
      return body;
    }

    function ConsolePanel(props) {
      var close = props && props.close;
      var [data, setData] = useState(null);
      var [rows, setRows] = useState([]);
      var [contractText, setContractText] = useState("");
      var [error, setError] = useState(null);
      var [busy, setBusy] = useState(false);
      var [busyLabel, setBusyLabel] = useState("");
      var [dialog, setDialog] = useState(null);
      var [checks, setChecks] = useState(null);
      var [notice, setNotice] = useState(null);
      var [selected, setSelected] = useState([]);
      var [reviewNote, setReviewNote] = useState("");
      var tokenRef = useRef(null);

      async function load() {
        try {
          var status = await apiGet("/status");
          tokenRef.current = status.csrfToken;
          setData(status);
          setError(null);
          var logs = await apiGet("/logs?limit=20");
          setRows(logs.rows || []);
        } catch (failure) {
          setError(String(failure.message || failure));
        }
      }

      useEffect(function () {
        load();
        var timer = setInterval(load, 5000);
        return function () { clearInterval(timer); };
      }, []);

      async function post(path, body) {
        var response = await fetch(API + path, {
          method: "POST",
          headers: { "content-type": "application/json", "x-coord-token": tokenRef.current || "" },
          body: JSON.stringify(body || {}),
        });
        var answer = await response.json();
        if (!response.ok) {
          var failure = new Error(answer.message || answer.error || ("HTTP " + response.status));
          failure.answer = answer;
          throw failure;
        }
        return answer;
      }

      async function run(action, label) {
        var started = Date.now();
        setBusy(true); setBusyLabel(label || "正在处理…"); setError(null); setNotice(null);
        try { await action(); await load(); } catch (failure) { setError(String(failure.message || failure)); } finally {
          var remaining = 600 - (Date.now() - started);
          if (remaining > 0) await new Promise(function (resolve) { setTimeout(resolve, remaining); });
          setBusy(false); setBusyLabel("");
        }
      }

      function confirmToggle(enabled) {
        if (!enabled) { setDialog({ kind: "off" }); return; }
        run(async function () { await post("/toggle", { enabled: true }); }, "正在开启多模型协作…");
      }

      function requestOff(safeStop) {
        run(async function () {
          try { await post("/toggle", { enabled: false, safeStop: safeStop }); }
          catch (failure) {
            if (failure.answer && failure.answer.ops) setError("有受控操作仍在运行：" + failure.answer.ops.map(function (op) { return op.kind; }).join(", ") + "。请选择 finish 或 abort。");
            else throw failure;
          }
          setDialog(null);
        }, safeStop ? "正在安全停止…" : "正在关闭…");
      }

      function review(decision) {
        var activate = decision === "pass";
        run(async function () {
          var answer = await post("/verify", { decision: decision, activate: activate, note: reviewNote || null });
          var result = answer.result || {};
          setChecks(result);
          if (decision === "reject") {
            setNotice({ ok: true, text: "已记录“不通过”" + (reviewNote ? "：" + reviewNote : "") + "；当前版本保持未验证、不注入契约。" });
          } else if (result.ok) {
            setNotice({ ok: true, text: "审核通过并已启用。" });
            setReviewNote("");
          } else {
            var failed = (result.checks || []).filter(function (item) { return !item.ok && !item.optional; }).map(function (item) { return item.id; });
            setError("验证未通过：" + (failed.join("、") || "未知原因"));
          }
          setDialog(null);
          await load();
        }, decision === "reject" ? "正在记录审核结果…" : "正在验证并启用…");
      }

      function installSelected() {
        run(async function () {
          var answer = await post("/extensions/install", { ids: activeSelected, confirm: true });
          setSelected([]);
          setDialog(null);
          if (answer.ok) {
            setNotice({ ok: true, text: "扩展已安装（仍为禁用）。请在列表点“验证并启用”。" });
          } else {
            var failed = (answer.results || []).filter(function (item) { return !item.ok; });
            setNotice({ ok: false, text: "安装失败：" + (failed.length ? failed.map(function (item) {
              return item.id + "（" + (item.error || item.stage || "未知原因") + "）";
            }).join("；") : "请查看状态与本地日志。") });
          }
        }, "正在安装扩展，可能需要下载…");
      }

      function activate(id) {
        run(async function () {
          var answer = await post("/extensions/activate", { id: id, confirm: true });
          if (answer.result && answer.result.ok === false) {
            setError("启用失败：" + (answer.result.status || "未知状态") + "（扩展仍为禁用）");
          } else if (answer.result && answer.result.restartRequired) {
            setNotice({ ok: true, text: "扩展已写入 Profile；重启 DSH 后生效。" });
          }
        }, "正在验证并启用扩展…");
      }

      function deactivateExtension(id) {
        run(async function () {
          var answer;
          try {
            answer = await post("/extensions/deactivate", { id: id, confirm: true });
          } catch (failure) {
            setDialog(null);
            setNotice({ ok: false, text: "停用失败：" + String(failure && failure.message ? failure.message : failure) });
            return;
          }
          setDialog(null);
          if (answer.result && answer.result.ok === false) {
            setNotice({ ok: false, text: "停用失败：" + (answer.result.status || "未知状态") + "（扩展仍为启用）" });
          } else if (answer.result && answer.result.restartRequired) {
            setNotice({ ok: true, text: "扩展已从 Profile 停用；重启 DSH 后生效，当前进程仍加载旧扩展。" });
          } else {
            setNotice({ ok: true, text: "扩展已停用，文件仍保留（已安装未启用）；可再次验证并启用。" });
          }
        }, "正在停用扩展…");
      }

      async function showContract() {
        try {
          var answer = await apiGet("/contract");
          setContractText(answer.text || "(contract unavailable)");
          setDialog({ kind: "contract" });
        } catch (failure) { setError(String(failure.message || failure)); }
      }

      if (!data && !error) return h("div", { style: S.page }, h("p", { style: S.small }, "正在读取插件状态…"));

      var status = data || {};
      var sw = status.switch || {};
      var ver = status.version || {};
      var contract = status.contract || {};
      var logs = status.logs || { counts: {} };
      var extensions = status.extensions || [];
      var selectable = extensions.filter(function (item) {
        return item.policy === "optional" && item.status === "not_installed" && item.installable !== false;
      });
      var activeSelected = selected.filter(function (id) {
        return selectable.some(function (item) { return item.id === id; });
      });
      var pending = sw.pending === true;
      var enabled = sw.enabled === true;

      return h("div", { style: S.page },
        h("div", { style: S.row },
          h("div", null,
            h("h2", { style: S.h2 }, "多模型协作"),
            h("p", { style: S.muted }, "DSH 原生插件 · 开关、规则契约、版本锁定、可选扩展、本地日志")),
          close ? h("button", { style: S.buttonGhost, onClick: close }, "关闭") : null),

        error ? h("div", { style: { ...S.card, borderColor: C.bad, color: C.bad } }, error) : null,

        busy ? h("div", { style: { ...S.card, borderColor: C.accent } },
          h("div", { style: S.row },
            h("strong", null, busyLabel || "正在处理…"),
            h("span", { style: S.small }, "请稍候，验证/安装可能需要几秒")),
          h("progress", { style: { width: "100%", height: "6px", accentColor: C.accent, marginTop: "8px" } }, null)) : null,

        notice ? h("div", { style: { ...S.card, borderColor: notice.ok ? C.ok : C.bad, color: notice.ok ? C.ok : C.bad } }, notice.text) : null,

        checks ? h("div", { style: { ...S.card, borderColor: checks.ok ? C.ok : C.bad } },
          h("div", { style: S.row },
            h("strong", null, checks.ok ? "验证通过" : "验证未通过"),
            h("span", { style: S.small }, checks.ok ? "运行版本 " + (checks.version || ver.running || "?") + " 已记录" : "请检查未通过项")),
          h("div", { style: { ...S.small, marginTop: "4px" } },
            checks.ok
              ? (checks.activated ? "状态已刷新；已验证版本已记录，开关状态见下方。" : "状态已刷新；可在下方确认启用。")
              : checks.checks.filter(function (item) { return !item.ok && !item.optional; }).map(function (item) { return item.id; }).join("、")),
          h("button", { style: { ...S.buttonGhost, marginTop: "8px" }, onClick: function () { setChecks(null); } }, "收起")) : null,

        pending ? h("div", { style: { ...S.card, borderColor: C.warn } },
          h("div", { style: S.row },
            h("div", null,
              h("h3", { style: S.h3 }, "新版本待审核"),
              h("div", { style: S.small }, "运行版本 " + (ver.running || "?") + " / 上次验证 " + (ver.verified || "无") + " · 摘要 " + (ver.digest || "?")),
              h("div", { style: { ...S.small, marginTop: "6px" } }, "一键审核：通过 = 验证并启用；不通过 = 记录原因、保持未验证（不注入契约、不注册工具）。")),
            h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } },
              h("button", { style: S.button, disabled: busy, onClick: function () { review("pass"); } }, busy ? "处理中…" : "✓ 通过并启用"),
              h("button", { style: S.buttonGhost, disabled: busy, onClick: function () { review("reject"); } }, "✗ 不通过"))),
          h("input", {
            value: reviewNote,
            placeholder: "不通过时填写原因（可选；会写入本地日志与状态）",
            onChange: function (event) { setReviewNote(event.target.value); },
            style: { marginTop: "8px", width: "100%", boxSizing: "border-box", background: C.chip, color: C.text, border: "1px solid " + C.line, borderRadius: "6px", padding: "6px 8px", fontSize: "12px" },
          }),
          data && data.verification ? h("div", { style: { ...S.small, marginTop: "6px", color: data.verification.decision === "pass" ? C.ok : C.bad } },
            "上次审核：" + (data.verification.decision === "pass" ? "通过" : "不通过") +
            " · " + (data.verification.by || "user") +
            " · " + relativeTime(data.verification.at) +
            (data.verification.note ? " · " + data.verification.note : "")) : null) : null,

        h("div", { style: S.card },
          h("div", { style: S.row },
            h("div", null,
              h("h3", { style: S.h3 }, "启用多模型协作约束"),
              h("div", { style: S.small }, enabled ? "已启用：宿主注入 40 条契约；会话确认前拒绝其他工具。" : "已关闭：DSH 保持原生行为。"),
              h("div", { style: { ...S.small, marginTop: "4px" } }, "覆盖：" + ((status.coverage || {}).covered || []).join("、") + "；不覆盖：" + ((status.coverage || {}).notCovered || []).join("、"))),
            h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } },
              pill(pending ? "覆盖受限" : (enabled ? "已启用" : "已关闭"), pending ? C.warn : (enabled ? C.ok : C.muted)),
              h("button", { style: enabled ? S.buttonGhost : S.button, disabled: busy || pending, onClick: function () { confirmToggle(!enabled); } }, enabled ? "关闭" : "开启")))),

        h("div", { style: S.card },
          h("div", { style: S.row },
            h("h3", { style: S.h3 }, "规则契约"),
            h("div", { style: { display: "flex", gap: "8px" } },
              pill(contract.ok ? "40 条已加载" : "契约无效", contract.ok ? C.ok : C.bad),
              h("button", { style: S.buttonGhost, onClick: showContract }, "查看契约"))),
          h("div", { style: S.small }, "契约哈希 " + (contract.hash ? contract.hash.slice(0, 16) : "?") + " · 当前 epoch " + (contract.epoch || 0) + " · 已确认会话 " + (contract.acknowledgedSessions || 0)),
          contract.ok ? null : h("div", { style: { color: C.bad, fontSize: "12px", marginTop: "6px" } }, (contract.errors || []).join("；"))),

        h("div", { style: S.card },
          h("h3", { style: S.h3 }, "可选扩展"),
          h("div", { style: S.small }, "只显示维护者声明的 optional 扩展；安装需确认，安装后仍为禁用，需单独验证并启用。"),
          extensions.length === 0 ? h("p", { style: S.small }, "没有已声明的可选扩展。") : h("table", { style: S.table },
            h("thead", null, h("tr", null,
              h("th", { style: S.th }, ""), h("th", { style: S.th }, "扩展"), h("th", { style: S.th }, "用途 / 来源"),
              h("th", { style: S.th }, "状态"), h("th", { style: S.th }, "操作"))),
            h("tbody", null, extensions.map(function (item) {
              var checked = item.status === "not_installed" && activeSelected.indexOf(item.id) >= 0;
              return h("tr", { key: item.id },
                h("td", { style: S.td }, h("input", { type: "checkbox", checked: checked, disabled: item.policy !== "optional" || item.status !== "not_installed" || item.installable === false, onChange: function (event) {
                  setSelected(function (previous) { return event.target.checked ? previous.concat([item.id]) : previous.filter(function (id) { return id !== item.id; }); });
                } })),
                h("td", { style: S.td }, h("strong", null, item.displayName), h("div", { style: S.small }, item.summary || "")),
                h("td", { style: S.td }, h("div", { style: S.small }, item.source || "未声明来源"), h("div", { style: S.small }, item.documentation || "")),
                h("td", { style: S.td }, pill(item.status, item.status === "enabled" ? C.ok : item.status === "not_installed" ? C.muted : C.warn), item.version ? h("div", { style: S.small }, "v" + item.version) : null),
                h("td", { style: S.td }, h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap" } },
                  item.status === "enabled" ? h("button", { style: S.buttonGhost, onClick: function () { setDialog({ kind: "extension-off", id: item.id, displayName: item.displayName }); } }, "停用") : null,
                  item.status === "installed_disabled" && item.eligible ? h("button", { style: S.buttonGhost, onClick: function () { activate(item.id); } }, "验证并启用") : null,
                  item.status === "unknown" ? h("span", { style: S.small }, "插件管理器不可用") : null)));
            }))),
          h("div", { style: { marginTop: "10px" } },
            h("button", { style: S.button, disabled: busy || activeSelected.length === 0, onClick: function () { setDialog({ kind: "install" }); } }, "安装所选（" + activeSelected.length + "）"))),

        h("div", { style: S.card },
          h("div", { style: S.row },
            h("h3", { style: S.h3 }, "本地日志"),
            h("div", { style: { display: "flex", gap: "8px" } },
              pill("I0 " + (logs.counts ? logs.counts.I0 : 0), C.bad),
              pill("I1 " + (logs.counts ? logs.counts.I1 : 0), C.warn),
              pill("I2 " + (logs.counts ? logs.counts.I2 : 0), C.accent),
              pill("I3 " + (logs.counts ? logs.counts.I3 : 0), C.muted))),
          h("div", { style: S.small }, "仅本机保存，默认在 Git 工作区之外；不会自动修复、上传或进入发布包。目录：" + (status.logRoot || "不可用") + (logs.logWriteFailed ? "（主日志写入失败，已降级）" : "")),
          rows.length === 0 ? h("p", { style: S.small }, "今天没有记录。") : h("table", { style: S.table },
            h("thead", null, h("tr", null,
              h("th", { style: S.th }, "时间"), h("th", { style: S.th }, "级别"), h("th", { style: S.th }, "事件"), h("th", { style: S.th }, "摘要"))),
            h("tbody", null, rows.slice(0, 12).map(function (row) {
              return h("tr", { key: row.event_id || (row.timestamp + row.summary) },
                h("td", { style: S.td }, String(row.timestamp || "").slice(11, 19)),
                h("td", { style: S.td }, pill(row.priority, priorityColor(row.priority))),
                h("td", { style: S.td }, row.event_type || ""),
                h("td", { style: S.td }, row.summary || ""));
            })))),

        h("div", { style: S.card },
          h("h3", { style: S.h3 }, "运行状态"),
          h("div", { style: S.small }, "核心引擎：" + ((status.core || {}).available ? "可用" : "不可用（门禁动作 fail-closed）") + " · Profile：" + (status.profile || "未知") + " · 平台：" + (status.platform || "?")),
          h("div", { style: { ...S.small, marginTop: "6px" } }, "这里只显示运行时信息；版本验证与启用请用上方“验证并启用”。")),

        checks ? h("div", { style: S.card },
          h("h3", { style: S.h3 }, "验证结果"),
          h("table", { style: S.table }, h("tbody", null, checks.checks.map(function (item) {
            return h("tr", { key: item.id },
              h("td", { style: S.td }, pill(item.ok ? "OK" : (item.optional ? "可选" : "失败"), item.ok ? C.ok : (item.optional ? C.muted : C.bad))),
              h("td", { style: S.td }, item.id),
              h("td", { style: S.td }, item.detail || ""));
          })))) : null,

        h("p", { style: { ...S.small, marginTop: "14px" } }, "边界：强制范围仅限本插件控制的系统提示、协调工具/守卫、人类安装/验证入口和 Python 门禁调用；宿主其他工具、直接 shell/网络、其他插件私有路径与模型推理不在范围内。"),

        dialog ? h("div", { style: S.dialog },
          h("div", { style: S.dialogBox },
            dialog.kind === "install" ? h("div", null,
              h("h3", { style: S.h3 }, "确认安装所选扩展"),
              h("p", { style: S.small }, "本次安装会写入当前 Profile，安装来源与权限如下；安装后扩展仍为禁用，启用需要单独确认。"),
              h("ul", { style: { fontSize: "12px", paddingLeft: "18px" } }, extensions.filter(function (item) { return activeSelected.indexOf(item.id) >= 0; }).map(function (item) {
                return h("li", { key: item.id }, h("strong", null, item.displayName), " · " + (item.source || "未声明来源") + " · 权限：" + ((item.permissions || []).join(", ") || "未声明") + " · 目标 Profile：" + (status.profile || "?"));
              })),
              busy ? h("div", { style: { marginTop: "12px" } },
                h("div", { style: S.small }, busyLabel || "正在安装扩展，可能需要下载…"),
                h("progress", { style: { width: "100%", height: "6px", accentColor: C.accent, marginTop: "6px" } }, null)) : null,
              h("div", { style: { display: "flex", gap: "8px", justifyContent: "flex-end", marginTop: "12px" } },
                h("button", { style: S.buttonGhost, disabled: busy, onClick: function () { setDialog(null); } }, "取消"),
                h("button", { style: S.button, disabled: busy, onClick: installSelected }, busy ? "安装中…" : "确认安装（禁用状态）"))) :
            dialog.kind === "extension-off" ? h("div", null,
              h("h3", { style: S.h3 }, "停用扩展"),
              h("p", { style: S.small }, "将停用 " + (dialog.displayName || dialog.id) + "；不会卸载，文件与 Profile 记录保留，之后可再次验证并启用。"),
              h("div", { style: { display: "flex", gap: "8px", justifyContent: "flex-end", marginTop: "12px" } },
                h("button", { style: S.buttonGhost, disabled: busy, onClick: function () { setDialog(null); } }, "取消"),
                h("button", { style: S.buttonDanger, disabled: busy, onClick: function () { deactivateExtension(dialog.id); } }, busy ? "停用中…" : "确认停用"))) :
            dialog.kind === "off" ? h("div", null,
              h("h3", { style: S.h3 }, "关闭总开关"),
              h("p", { style: S.small }, "关闭后新任务恢复 DSH 原生行为；不会删除历史审批、证据或本地日志。若有受控操作在运行，请选择收尾方式。"),
              h("div", { style: { display: "flex", gap: "8px", justifyContent: "flex-end" } },
                h("button", { style: S.buttonGhost, onClick: function () { setDialog(null); } }, "取消"),
                h("button", { style: S.buttonGhost, onClick: function () { requestOff("finish"); } }, "等待完成"),
                h("button", { style: S.buttonDanger, onClick: function () { requestOff("abort"); } }, "中止并关闭"))) :
            h("div", null,
              h("h3", { style: S.h3 }, "规则契约"),
              h("pre", { style: S.code }, contractText || "(unavailable)"),
              h("div", { style: { display: "flex", justifyContent: "flex-end" } },
                h("button", { style: S.button, onClick: function () { setDialog(null); } }, "关闭"))))) : null
      );
    }

    function statusColor(status) {
      if (status === "done") return C.ok;
      if (status === "review") return "#a371f7";
      if (status === "rework" || status === "failed") return C.bad;
      if (status === "running") return C.accent;
      if (status === "ready") return C.warn;
      return C.muted;
    }

    function shortText(value, limit) {
      var text = String(value || "");
      return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
    }

    function errorText(body, fallback) {
      if (!body) return fallback;
      return String(body.message || body.error || body.reason || fallback);
    }

    function findingText(finding) {
      if (finding === null || finding === undefined) return "";
      if (typeof finding === "string") return finding;
      return String(finding.summary || finding.message || finding.issue || finding.detail || JSON.stringify(finding));
    }

    function relativeTime(iso) {
      var at = Date.parse(String(iso || ""));
      if (!isFinite(at)) return "";
      var seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
      if (seconds < 60) return seconds + "s 前";
      if (seconds < 3600) return Math.round(seconds / 60) + "m 前";
      if (seconds < 86400) return Math.round(seconds / 3600) + "h 前";
      return Math.round(seconds / 86400) + "d 前";
    }

    function normaliseQuery(value) {
      return String(value || "").toLowerCase().replace(/[\s\-_./]+/g, "");
    }

    function routeKey(route) {
      return String(route.lane || "dsh") + "|" + String(route.provider || "") + "|" + String(route.model || "");
    }

    function WorkflowPanel(props) {
      var close = props && props.close;
      var [boards, setBoards] = useState([]);
      var [tasks, setTasks] = useState({});
      var [expanded, setExpanded] = useState(null);
      var [selectedNode, setSelectedNode] = useState(null);
      var [models, setModels] = useState(null);
      var [verdict, setVerdict] = useState(null);
      var [query, setQuery] = useState("");
      var [listOpen, setListOpen] = useState(false);
      var [cursor, setCursor] = useState(0);
      var [routeValue, setRouteValue] = useState("");
      var [error, setError] = useState(null);
      var [notice, setNotice] = useState(null);
      var [busy, setBusy] = useState(false);
      var [polling, setPolling] = useState(false);
      var tokenRef = useRef(null);
      var streamRef = useRef(null);
      var pollRef = useRef(null);
      var mountedRef = useRef(false);

      function closeStream() {
        if (streamRef.current) {
          streamRef.current.onmessage = null;
          streamRef.current.onerror = null;
          streamRef.current.close();
          streamRef.current = null;
        }
      }

      function stopPolling() {
        if (pollRef.current !== null) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        if (mountedRef.current) setPolling(false);
      }

      function mergeFrame(frame) {
        if (!frame) return;
        if (Array.isArray(frame.boards)) setBoards(frame.boards);
        if (frame.tasks && typeof frame.tasks === "object") {
          setTasks(function (previous) {
            var next = { ...previous };
            Object.keys(frame.tasks).forEach(function (key) { next[key] = frame.tasks[key]; });
            return next;
          });
        }
      }

      async function loadSnapshot() {
        try {
          var listed = await apiGet("/workflow/boards");
          if (mountedRef.current) setBoards(Array.isArray(listed.boards) ? listed.boards : []);
          var all = await apiGet("/workflow/tasks?ids=all");
          if (mountedRef.current && all.tasks) setTasks(all.tasks);
          return true;
        } catch (failure) {
          if (mountedRef.current) setError("读取工作流失败：" + String(failure.message || failure));
          return false;
        }
      }

      async function loadModels() {
        try {
          var answer = await apiGet("/models");
          if (mountedRef.current) setModels(answer);
        } catch (failure) {
          if (mountedRef.current) setModels({ ok: false, routes: [], reason: String(failure.message || failure) });
        }
      }

      async function loadVerdict(taskId, nodeId) {
        if (!taskId || !nodeId) { setVerdict(null); return; }
        try {
          var answer = await apiGet("/workflow/options?task=" + encodeURIComponent(taskId) + "&node=" + encodeURIComponent(nodeId));
          if (mountedRef.current) setVerdict(answer);
        } catch (failure) {
          if (mountedRef.current) setVerdict({ ok: false, options: [], reason: String(failure.message || failure) });
        }
      }

      function startPolling() {
        closeStream();
        if (pollRef.current === null) {
          if (mountedRef.current) setPolling(true);
          pollRef.current = setInterval(function () { loadSnapshot(); }, 5000);
        }
      }

      function openStream() {
        closeStream();
        stopPolling();
        try {
          var stream = new EventSource(API + "/workflow/events?tasks=all");
          streamRef.current = stream;
          stream.onmessage = function (event) {
            try {
              mergeFrame(JSON.parse(event.data));
              if (mountedRef.current) setError(null);
            } catch (failure) {
              startPolling();
            }
          };
          stream.onerror = function () { startPolling(); };
        } catch (failure) {
          startPolling();
        }
      }

      useEffect(function () {
        mountedRef.current = true;
        (async function () {
          try {
            var status = await apiGet("/status");
            tokenRef.current = status.csrfToken || null;
          } catch (failure) {
            setError("读取安全令牌失败：" + String(failure.message || failure));
          }
          await Promise.all([loadSnapshot(), loadModels()]);
          if (mountedRef.current) openStream();
        })();
        return function () {
          mountedRef.current = false;
          closeStream();
          if (pollRef.current !== null) { clearInterval(pollRef.current); pollRef.current = null; }
        };
      }, []);

      var activeRow = expanded ? tasks[expanded] : null;
      var nodeList = activeRow && Array.isArray(activeRow.nodes) ? activeRow.nodes : [];
      var pickedNode = selectedNode ? nodeList.filter(function (node) { return String(node.id) === String(selectedNode); })[0] : null;
      var options = verdict && Array.isArray(verdict.options) ? verdict.options : [];
      var rows = options.length
        ? options
        : (models && Array.isArray(models.routes) ? models.routes : []);

      useEffect(function () {
        if (!pickedNode) { setVerdict(null); setRouteValue(""); return; }
        var assignee = pickedNode.assignee || {};
        setRouteValue(assignee.provider && assignee.model ? routeKey(assignee) : "");
      }, [expanded, selectedNode]);

      useEffect(function () {
        if (pickedNode && (options.length === 0 || (verdict && verdict.task !== expanded))) {
          loadVerdict(expanded, pickedNode.id);
        }
      }, [expanded, selectedNode, activeRow && activeRow.revision]);

      var filtered = rows.filter(function (route) {
        if (!query) return true;
        var haystack = normaliseQuery(String(route.name || "") + String(route.model || "") + String(route.provider || ""));
        return haystack.indexOf(normaliseQuery(query)) >= 0;
      });
      var selectable = filtered.filter(function (route) { return route.eligible !== false; });

      async function workflowPost(path, body) {
        var response = await fetch(API + path, {
          method: "POST",
          headers: { "content-type": "application/json", "x-coord-token": tokenRef.current || "" },
          body: JSON.stringify(body),
        });
        var answer = await response.json();
        if (!response.ok || answer.ok === false) {
          var failure = new Error(answer.message || answer.reason || answer.error || ("HTTP " + response.status));
          failure.status = response.status;
          throw failure;
        }
        return answer;
      }

      async function pinModel() {
        if (!activeRow || !pickedNode || !routeValue) return;
        var parts = routeValue.split("|");
        setBusy(true); setError(null); setNotice(null);
        try {
          await workflowPost("/workflow/pin", {
            task: activeRow.task_id,
            node: pickedNode.id,
            lane: parts[0],
            provider: parts[1],
            model: parts.slice(2).join("|"),
            by: "user",
            expected_revision: activeRow.revision,
          });
          await loadSnapshot();
          if (mountedRef.current) setNotice("模型已指定");
        } catch (failure) {
          if (mountedRef.current) setError(String(failure.message || failure));
          if (failure.status === 409) await loadSnapshot();
        } finally {
          if (mountedRef.current) setBusy(false);
        }
      }

      async function deliver() {
        if (!activeRow || !activeRow.can_deliver) return;
        setBusy(true); setError(null); setNotice(null);
        try {
          var answer = await workflowPost("/workflow/node", {
            task: activeRow.task_id,
            node: pickedNode ? pickedNode.id : "",
            op: "deliver",
            expected_revision: activeRow.revision,
          });
          await loadSnapshot();
          var commit = answer.commit || (activeRow.delivery && activeRow.delivery.commit) || "";
          if (mountedRef.current) setNotice("已交付" + (commit ? " " + commit : ""));
        } catch (failure) {
          if (mountedRef.current) setError(String(failure.message || failure));
        } finally {
          if (mountedRef.current) setBusy(false);
        }
      }

      function commitRoute(route) {
        if (!route || route.eligible === false) {
          if (mountedRef.current) setError(route && route.reason ? route.reason : "该模型不能指派给这个节点");
          return;
        }
        setRouteValue(routeKey(route));
        setListOpen(false);
        setQuery("");
      }

      function onKeyDown(event) {
        if (event.key === "Escape") { setListOpen(false); return; }
        if (!listOpen) { if (event.key === "ArrowDown") setListOpen(true); return; }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          var step = event.key === "ArrowDown" ? 1 : -1;
          var index = cursor;
          for (var guard = 0; guard < filtered.length; guard += 1) {
            index = (index + step + filtered.length) % filtered.length;
            if (filtered[index] && filtered[index].eligible !== false) break;
          }
          setCursor(index);
          return;
        }
        if (event.key === "Enter") {
          event.preventDefault();
          var hit = filtered[cursor];
          if (hit && hit.eligible !== false) commitRoute(hit);
          else if (hit) commitRoute(hit);
        }
      }

      function boardCard(summary) {
        var task = tasks[summary.task_id] || null;
        var isOpen = expanded === summary.task_id;
        var activity = task && task.activity ? task.activity : null;
        var progress = task && task.progress ? task.progress : summary.progress;
        var history = task && Array.isArray(task.history) ? task.history : [];
        var tail = isOpen ? history.slice(-20) : history.slice(-3);
        var current = activity && Array.isArray(activity.current) ? activity.current : [];
        var blocked = activity && Array.isArray(activity.blocked) ? activity.blocked : [];
        var nodes = task && Array.isArray(task.nodes) ? task.nodes : [];
        var edges = workflowEdges(nodes);
        var layout = layoutDag(nodes, edges);
        var stale = task && task.activity && task.activity.stale_seconds > 120;
        return h("div", {
          key: summary.task_id,
          style: {
            ...S.card,
            margin: isOpen ? "12px 0" : "8px 0",
            padding: isOpen ? "14px 16px" : "10px 12px",
            borderColor: summary.status === "failed" ? C.bad : (isOpen ? "#ffffff44" : C.line),
          },
        },
          h("div", {
            style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px", cursor: "pointer" },
            onClick: function () {
              setExpanded(isOpen ? null : summary.task_id);
              setSelectedNode(null);
            },
          },
            h("div", { style: { minWidth: 0 } },
              h("div", { style: { fontSize: "13px", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, summary.title || summary.task_id),
              h("div", { style: { display: "flex", gap: "6px", alignItems: "center", marginTop: "4px", flexWrap: "wrap" } },
                pill(summary.status, statusColor(summary.status)),
                h("span", { style: S.small }, String(progress.done || 0) + "/" + String(progress.total || 0)),
                h("span", { style: { ...S.small, color: stale ? C.warn : C.muted } }, relativeTime(summary.updated_at)),
                summary.can_deliver ? pill("可交付", C.ok) : null)),
            h("span", { style: S.small }, isOpen ? "收起" : "展开")),
          task && current.length ? h("div", { style: { marginTop: "8px" } },
            current.slice(0, 3).map(function (node) {
              var assignee = node.assignee || {};
              return h("div", { key: node.id, style: { ...S.small, color: C.text } },
                "进行中：" + (node.title || node.id) + " · " + (assignee.provider || "?") + "/" + (assignee.model || "?") +
                (node.stale_seconds !== null && node.stale_seconds !== undefined ? " · 已 " + node.stale_seconds + "s" : ""));
            })) : null,
          !isOpen && blocked.length ? h("div", { style: { ...S.small, marginTop: "6px", color: C.warn } },
            "等待：" + blocked.slice(0, 2).map(function (item) { return (item.title || item.id) + " ← " + item.reason; }).join("；")) : null,
          tail.length ? h("div", { style: { marginTop: "8px", display: "flex", flexDirection: "column", gap: "2px" } },
            tail.map(function (item, index) {
              return h("div", { key: index, style: { ...S.small, fontFamily: "ui-monospace,Consolas,monospace" } },
                String(item.op || "?") + " · " + shortText(String(item.detail || ""), 60) + " · " + relativeTime(item.at));
            })) : null,
          isOpen ? h("div", null,
            layout.nodes.length === 0 ? h("div", { style: { ...S.small, marginTop: "10px" } }, "任务中还没有节点。") :
            h("div", { style: { ...S.card, marginTop: "10px", overflow: "auto", padding: "6px" } },
              h("svg", { width: layout.width, height: layout.height, style: { display: "block", minWidth: layout.width + "px" } },
                h("defs", null,
                  h("marker", { id: "workflow-arrow", markerWidth: "8", markerHeight: "8", refX: "7", refY: "4", orient: "auto", markerUnits: "strokeWidth" },
                    h("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: C.muted }))),
                layout.edges.map(function (edge, index) {
                  var edgeColor = edge.kind === "rework" ? C.bad : edge.kind === "review" ? "#a371f7" : C.muted;
                  return h("path", {
                    key: edge.kind + ":" + edge.from + ":" + edge.to + ":" + index,
                    d: edge.path, fill: "none", stroke: edgeColor, strokeWidth: "1.5",
                    strokeDasharray: edge.kind === "review" ? "2 5" : edge.kind === "rework" ? "7 5" : undefined,
                    markerEnd: "url(#workflow-arrow)",
                  });
                }),
                layout.nodes.map(function (position) {
                  var node = nodes.filter(function (item) { return String(item.id) === position.id; })[0] || {};
                  var color = statusColor(node.status);
                  var assignee = node.assignee || {};
                  var label = assignee.lane && assignee.model ? assignee.lane + "/" + assignee.model : "未指派";
                  var isPicked = selectedNode && String(selectedNode) === position.id;
                  return h("g", {
                    key: position.id,
                    transform: "translate(" + position.x + " " + position.y + ")",
                    onClick: function () { setSelectedNode(position.id); },
                    style: { cursor: "pointer" },
                  },
                    h("rect", { width: position.w, height: position.h, rx: "8", fill: C.card, stroke: isPicked ? "#ffffff" : color, strokeWidth: isPicked ? "2.5" : "1.5" }),
                    h("text", { x: "12", y: "21", fill: C.text, fontSize: "12", fontWeight: "600" }, shortText(node.title || node.id, 18)),
                    h("rect", { x: "10", y: "34", width: "112", height: "20", rx: "10", fill: C.chip }),
                    h("text", { x: "18", y: "48", fill: C.muted, fontSize: "10" }, shortText(label, 19)),
                    node.attempt > 1 ? h("g", null,
                      h("rect", { x: "128", y: "35", width: "30", height: "18", rx: "9", fill: color }),
                      h("text", { x: "143", y: "48", fill: "#ffffff", fontSize: "10", textAnchor: "middle" }, "×" + node.attempt)) : null);
                }))),
            pickedNode ? h("div", { style: { ...S.card, marginTop: "10px" } },
              h("div", { style: S.row },
                h("div", null,
                  h("h3", { style: S.h3 }, pickedNode.title || pickedNode.id),
                  h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap" } },
                    pill(pickedNode.kind || "work", C.muted),
                    pill(pickedNode.status || "pending", statusColor(pickedNode.status)),
                    pill("尝试 " + String(pickedNode.attempt || 1), C.muted),
                    pickedNode.updated_at ? pill(relativeTime(pickedNode.updated_at), C.muted) : null)),
                pickedNode.assignee ? h("div", { style: { textAlign: "right" } },
                  h("div", { style: S.small }, (pickedNode.assignee.provider || "?") + "/" + (pickedNode.assignee.model || "?")),
                  pickedNode.assignee.source === "manual" ? pill("手动", C.accent) : null) : h("span", { style: S.small }, "未指派")),
              pickedNode.assignee && pickedNode.assignee.violation ? h("div", { style: { ...S.small, color: C.bad, marginTop: "6px" } },
                "当前指派已违规：" + String(pickedNode.assignee.violation.reason || pickedNode.assignee.violation)) : null,
              verdict && verdict.kind === "review" ? h("div", { style: { ...S.small, marginTop: "8px" } },
                "评审节点：可选 " + String(verdict.options.filter(function (item) { return item.eligible !== false; }).length) +
                " / 不可选 " + String(verdict.options.filter(function (item) { return item.eligible === false; }).length) +
                (verdict.producer && verdict.producer.model ? " · 产出方 " + verdict.producer.model : "")) : null,
              pickedNode.result && pickedNode.result.summary ? h("div", { style: { ...S.small, marginTop: "8px" } },
                h("strong", { style: { color: C.text } }, "最近结果："), String(pickedNode.result.summary)) : null,
              h("div", { style: { marginTop: "10px", position: "relative" } },
                h("div", { style: { display: "flex", gap: "6px", alignItems: "center" } },
                  h("input", {
                    value: listOpen ? query : (function () {
                      var hit = rows.filter(function (route) { return routeKey(route) === routeValue; })[0];
                      return hit ? (hit.name || hit.model) : "";
                    })(),
                    placeholder: "输入模型名 / 厂商名…",
                    onFocus: function () { setListOpen(true); setQuery(""); setCursor(0); },
                    onChange: function (event) { setQuery(event.target.value); setListOpen(true); },
                    onKeyDown: onKeyDown,
                    style: { flex: "1 1 auto", minWidth: "180px", background: C.chip, color: C.text, border: "1px solid " + C.line, borderRadius: "6px", padding: "6px 8px", fontSize: "12px" },
                  }),
                  h("button", { style: S.button, disabled: busy || !routeValue, onClick: pinModel }, busy ? "指定中…" : "指定")),
                listOpen ? h("div", {
                  style: { position: "absolute", zIndex: 5, left: 0, right: 0, marginTop: "4px", maxHeight: "260px", overflow: "auto", background: C.card, border: "1px solid " + C.line, borderRadius: "8px", boxShadow: "0 8px 24px #0008" },
                },
                  filtered.length === 0 ? h("div", { style: { ...S.small, padding: "10px" } }, "未找到匹配的模型") : null,
                  filtered.map(function (route, index) {
                    var blockedRow = route.eligible === false;
                    return h("div", {
                      key: routeKey(route) + ":" + index,
                      onMouseDown: function (event) { event.preventDefault(); commitRoute(route); },
                      style: {
                        padding: "7px 10px",
                        cursor: blockedRow ? "not-allowed" : "pointer",
                        background: index === cursor && !blockedRow ? C.chip : "transparent",
                        opacity: blockedRow ? 0.55 : 1,
                        borderBottom: "1px solid " + C.line,
                      },
                    },
                      h("div", { style: { display: "flex", justifyContent: "space-between", gap: "8px" } },
                        h("span", { style: { fontSize: "12px" } }, route.name || route.model),
                        h("span", { style: { ...S.small, fontFamily: "ui-monospace,Consolas,monospace" } }, String(route.provider || "") + "/" + String(route.model || ""))),
                      blockedRow ? h("div", { style: { fontSize: "11px", color: C.bad, marginTop: "2px" } }, "⛔ " + String(route.reason || "不可选")) : null,
                      !blockedRow && route.family ? h("div", { style: { fontSize: "11px", color: C.muted, marginTop: "2px" } }, "family: " + route.family) : null);
                  }),
                  h("div", { style: { ...S.small, padding: "8px 10px" } },
                    "常用优先；输入可过滤 " + rows.length + " 条；不可选的模型会写明理由。")) : null)) : null) : null);
      }

      return h("div", { style: S.page },
        h("div", { style: S.row },
          h("div", null,
            h("h2", { style: S.h2 }, "工作流"),
            h("div", { style: S.small },
              "共 " + boards.length + " 条任务" +
              (boards.filter(function (item) { return item.status === "running" || item.status === "reviewing"; }).length
                ? " · 进行中 " + boards.filter(function (item) { return item.status === "running" || item.status === "reviewing"; }).length
                : "") +
              (polling ? " · 轮询" : " · 实时"))),
          h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } },
            h("button", { style: S.buttonGhost, disabled: busy, onClick: function () { loadSnapshot(); loadModels(); if (!streamRef.current) openStream(); } }, "刷新"),
            expanded ? h("button", { style: S.button, disabled: busy || !(tasks[expanded] && tasks[expanded].can_deliver), onClick: deliver }, busy ? "处理中…" : "交付") : null,
            close ? h("button", { style: S.buttonGhost, onClick: close }, "关闭") : null)),
        error ? h("div", { style: { ...S.card, borderColor: C.bad, color: C.bad } }, error) : null,
        notice ? h("div", { style: { ...S.card, borderColor: C.ok, color: C.ok } }, notice) : null,
        boards.length === 0
          ? h("div", { style: S.card },
            h("h3", { style: S.h3 }, "还没有工作流任务"),
            h("div", { style: S.small }, "用 coordination({action:\"workflow\", op:\"plan\"}) 创建任务计划；同一工作区可以同时推进多条，都会显示在这一页。"))
          : boards.map(boardCard));
    }

    function Panel(props) {
      var [view, setView] = useState("workflow");
      var selectedStyle = { ...S.buttonGhost, background: C.accent, borderColor: C.accent, color: "#fff" };
      return h("div", { style: { height: "100%", minHeight: 0, display: "flex", flexDirection: "column", color: C.text } },
        h("div", { style: { display: "flex", padding: "12px 22px 0", flex: "0 0 auto" } },
          h("div", { style: { display: "inline-flex", border: "1px solid " + C.line, borderRadius: "8px", padding: "2px", gap: "2px", background: C.card } },
            h("button", { style: view === "workflow" ? selectedStyle : S.buttonGhost, onClick: function () { setView("workflow"); } }, "工作流"),
            h("button", { style: view === "console" ? selectedStyle : S.buttonGhost, onClick: function () { setView("console"); } }, "控制台"))),
        h("div", { style: { flex: "1 1 auto", minHeight: 0, overflow: "hidden" } },
          view === "workflow" ? h(WorkflowPanel, props || {}) : h(ConsolePanel, props || {})));
    }

    function CoordinationTitle() {
      return h("span", null, "多模型协作");
    }

    function registerSlot(ctx, name, options, component, label) {
      try {
        ctx.slots.inject(name, function () {
          try {
            return ctx.slots.register(options, component);
          } catch (error) {
            reportClientError(label + " registration", error);
            return function () {};
          }
        });
      } catch (error) {
        reportClientError(label + " injection", error);
      }
    }

    function apply(ctx) {
      try {
        ctx.effect(function () {
          return ctx.sidebarRightTabs.register({
            id: "multimodel-coordination",
            kind: "multimodel-coordination",
            priority: "builtin",
            title: function () { return "多模型协作"; },
            guide: [{
              id: "open",
              order: 30,
              title: function () { return "多模型协作"; },
              description: function () { return "开关、规则契约、可选扩展与本地日志"; },
            }],
          });
        }, "multimodel-coordination: tab type");
      } catch (error) {
        reportClientError("right-sidebar tab type", error);
      }
      registerSlot(ctx, "sidebar.right.pane.tab", {
        name: "sidebar.right.pane.tab",
        key: "multimodel-coordination",
      }, Panel, "right-sidebar tab");
      registerSlot(ctx, "sidebar.right.pane.tab.title", {
        name: "sidebar.right.pane.tab.title",
        key: "multimodel-coordination",
      }, CoordinationTitle, "right-sidebar title");
    }

    function reportClientError(where, error) {
      try {
        if (typeof console !== "undefined" && console.error) {
          console.error("[multimodel-coordination] " + where + " failed", error);
        }
      } catch { /* console is optional */ }
    }

    exports.inject = ["slots", "sidebarRightTabs"];
    exports.layoutDag = layoutDag;
    exports.apply = apply;
    return module.exports;
  }
});
