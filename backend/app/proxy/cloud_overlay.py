"""ComfyUI 页面悬浮「云端任务」组件（方案二：独立小组件，不集成原生 Job Queue）。

桥接器把云端任务的进度/终态事件经 WS 注入浏览器（ws_relay.send_event），
但 ComfyUI 原生 Job Queue 面板（JobQueueStore）只登记前端自己发起的提交，
云端任务对它不可见。本模块在透传的 ComfyUI 根页面注入一段自包含脚本，
在界面右下角悬浮显示在途云端任务（prompt_id / 工作流名 / 已运行秒数 / 进度），
数据源与原生面板独立：

- 轮询 ``/api/v1/requests?status=all&limit=50`` 取在途任务（queued/running 且
  final_backend=modal），每 3 秒刷新；
- 订阅 ``/ws``（页面已有连接，复用同一 clientId）不重复——脚本只读轮询，
  进度数字从请求记录的 started_ms 推算已运行时长，无需解析 WS 事件；
- 无在途任务时悬浮条自动隐藏，不遮挡 ComfyUI 编辑器。

样式与 ComfyUI 暗色主题接近（z-index 低于弹窗），注入失败不影响页面其余功能。
"""

from __future__ import annotations

OVERLAY_SCRIPT = """
<script>
(function () {
  try {
    if (window.__cbCloudOverlay) return;
    window.__cbCloudOverlay = true;

    var POLL_MS = 3000;
    var panel = null;
    var listEl = null;
    var progressMap = {};   // prompt_id -> {v, m}（WS progress 事件）
    var nodeMap = {};       // prompt_id -> 当前执行节点名

    function connectWs() {
      var app = window.app;
      var clientId = app && app.api && app.api.clientId;
      if (!clientId) { setTimeout(connectWs, 1500); return; }
      var proto = location.protocol === "https:" ? "wss" : "ws";
      var ws = new WebSocket(proto + "://" + location.host + "/ws?clientId=" + encodeURIComponent(clientId));
      ws.onmessage = function (ev) {
        try {
          var msg = JSON.parse(ev.data);
          var d = msg.data || {};
          if (!d.prompt_id) return;
          if (msg.type === "progress") {
            progressMap[d.prompt_id] = {
              v: d.value || 0,
              m: d.max || d.max_value || 0,
            };
          } else if (msg.type === "executing" && d.node) {
            nodeMap[d.prompt_id] = String(d.node);
          } else if (msg.type === "execution_success" || msg.type === "execution_error"
                     || msg.type === "execution_interrupted") {
            delete progressMap[d.prompt_id];
            delete nodeMap[d.prompt_id];
          }
        } catch (e) {}
      };
      ws.onclose = function () { setTimeout(connectWs, 3000); };
    }
    connectWs();

    function ensurePanel() {
      if (panel) return panel;
      panel = document.createElement("div");
      panel.id = "cb-cloud-overlay";
      panel.style.cssText = [
        "position:fixed", "right:16px", "bottom:120px", "z-index:9000",
        "background:rgba(28,28,32,0.95)", "border:1px solid rgba(255,255,255,0.12)",
        "border-radius:8px", "padding:10px 14px", "color:#e8e8ea",
        "font:12px/1.5 -apple-system,'Segoe UI',sans-serif",
        "min-width:220px", "max-width:320px", "box-shadow:0 4px 16px rgba(0,0,0,0.4)",
        "display:none", "pointer-events:auto",
      ].join(";");
      var title = document.createElement("div");
      title.textContent = "Cloud Tasks";
      title.style.cssText = "font-weight:600;margin-bottom:6px;opacity:0.9;";
      panel.appendChild(title);
      listEl = document.createElement("div");
      panel.appendChild(listEl);
      document.body.appendChild(panel);
      return panel;
    }

    function fmtDuration(startedMs) {
      if (!startedMs) return "";
      var s = Math.max(0, Math.floor((Date.now() - startedMs) / 1000));
      if (s < 60) return s + "s";
      var m = Math.floor(s / 60);
      return m + "m" + (s % 60) + "s";
    }

    function render(items) {
      var active = items.filter(function (r) {
        return r.final_backend === "modal" &&
               (r.status === "queued" || r.status === "running");
      });
      if (!active.length) {
        if (panel) panel.style.display = "none";
        return;
      }
      var p = ensurePanel();
      listEl.innerHTML = "";
      active.forEach(function (r) {
        var pid = String(r.prompt_id);
        var row = document.createElement("div");
        row.style.cssText = "margin:4px 0;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.08);";
        var name = document.createElement("div");
        var statusText = r.status === "running"
          ? "running " + fmtDuration(r.started_ms)
          : "queued";
        name.textContent = (r.workflow_name || "Untitled") + "  [" + statusText + "]";
        name.style.cssText = "font-size:11px;opacity:0.85;";
        var prog = progressMap[pid];
        if (r.status === "running" && prog && prog.m > 0) {
          var pct = Math.min(100, Math.round((prog.v / prog.m) * 100));
          var barWrap = document.createElement("div");
          barWrap.style.cssText = "background:rgba(255,255,255,0.12);border-radius:3px;height:6px;margin:4px 0;overflow:hidden;";
          var bar = document.createElement("div");
          bar.style.cssText = "background:#4f8ef7;height:100%;width:" + pct + "%;transition:width 0.4s;";
          barWrap.appendChild(bar);
          row.appendChild(barWrap);
          var nodeLine = document.createElement("div");
          nodeLine.textContent = pct + "%  (" + prog.v + "/" + prog.m + ")" +
            (nodeMap[pid] ? "  node " + nodeMap[pid] : "");
          nodeLine.style.cssText = "font-size:10px;opacity:0.7;font-family:monospace;";
          row.appendChild(nodeLine);
        }
        var id = document.createElement("div");
        id.textContent = pid.slice(0, 8) + "..." +
          (r.model_name ? "  " + r.model_name : "");
        id.style.cssText = "font-size:10px;opacity:0.6;font-family:monospace;";
        row.appendChild(name);
        row.appendChild(id);
        listEl.appendChild(row);
      });
      p.style.display = "block";
    }

    function poll() {
      fetch("/api/v1/requests?status=all&limit=50")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && d.items) render(d.items); })
        .catch(function () {});
    }

    setInterval(poll, POLL_MS);
    setTimeout(poll, 1500);
  } catch (e) { /* 注入失败不影响页面 */ }
})();
</script>
"""


def enabled() -> bool:
    """悬浮组件开关；默认开启（无在途任务时不可见，不打扰）。"""
    return True
