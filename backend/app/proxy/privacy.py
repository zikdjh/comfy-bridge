"""云端提交前隐私确认（P2-5）。

桥接器是数据面透明代理，浏览器直接向 ComfyUI `/prompt` 提交，不经过管理台
SPA，因此确认必须由后端拦截层完成：首次把某工作流分流到 Modal 时返回
``409 privacy_confirmation_required``，调用方（前端扩展/脚本）带
``X-Bridge-Privacy-Confirmed: 1`` 重试即放行。

状态按浏览器保存在 localStorage 由前端侧负责；后端只判定本次请求是否携带
确认标记，不落任何隐私状态，保持无状态、可多实例部署。
"""

from __future__ import annotations

from ..core.config import get_settings

# 请求头名与大小写无关；值为 "1" / "true" / "yes" 均视为已确认。
CONFIRM_HEADER = "x-bridge-privacy-confirmed"
_TRUE_VALUES = {"1", "true", "yes", "on"}

DATA_SCOPE = (
    "本次提交会把以下数据发送到 Modal 云端：工作流图快照（节点、连线和参数）、"
    "输入文件（LoadImage 等引用的本地图片/音频）、必要的请求元数据（工作流名、"
    "客户端 ID、路由决策留痕）以及云端执行日志。模型权重仍留在 Modal 数据卷，"
    "不会因此上传你的整个本地磁盘；但工作流中可能包含路径、提示词和模型名，"
    "请确认内容可以离开本机。"
)

TITLE = "云端执行数据边界确认"

CONFIRM_PARAM = "bridge_privacy_confirmed"

# 注入到 ComfyUI 根页面的一次性确认脚本：拦截 fetch，遇到后端的 409 挑战时
# 弹一次原生确认框；用户确认后带请求头重试，并按浏览器记忆避免重复弹窗。
# 注入失败不影响页面其余功能（脚本整体 try/catch）。
INJECT_SCRIPT = """
<script>
(function () {
  try {
    var KEY = "cb_privacy_confirmed";
    if (window.__cbPrivacyHooked) return;
    window.__cbPrivacyHooked = true;
    function confirmed() {
      try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; }
    }
    function remember() {
      try { localStorage.setItem(KEY, "1"); } catch (e) {}
    }
    var origFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      var req = init || {};
      var headers = new Headers(req.headers || {});
      if (confirmed()) headers.set("X-Bridge-Privacy-Confirmed", "1");
      req = Object.assign({}, req, { headers: headers });
      return origFetch(input, req).then(function (resp) {
        if (resp.status !== 409) return resp;
        return resp.clone().json().then(function (body) {
          if (!body || body.error !== "privacy_confirmation_required") return resp;
          var detail = body.detail || "本次提交会把工作流与输入数据发送到 Modal 云端。";
          if (!window.confirm(detail + "\\n\\n确认继续提交到云端？")) return resp;
          remember();
          headers.set("X-Bridge-Privacy-Confirmed", "1");
          return origFetch(input, Object.assign({}, req, { headers: headers }));
        }).catch(function () { return resp; });
      });
    };
  } catch (e) { /* 注入脚本失败不阻断 ComfyUI 页面 */ }
})();
</script>
"""


def enabled() -> bool:
    """是否启用提交前确认；配置缺失时默认开启（安全默认）。"""
    return bool(getattr(get_settings().bridge, "privacy_confirm", True))


def _confirmed(request) -> bool:
    raw = request.headers.get(CONFIRM_HEADER, "")
    return raw.strip().lower() in _TRUE_VALUES


def challenge(request) -> dict | None:
    """返回需确认的响应体；已确认或开关关闭时返回 None。"""
    if not enabled() or _confirmed(request):
        return None
    return {
        "error": "privacy_confirmation_required",
        "message": TITLE,
        "detail": DATA_SCOPE,
        "confirm_header": CONFIRM_HEADER,
        "confirm_param": CONFIRM_PARAM,
        "scope": ["workflow_graph", "input_files", "metadata", "cloud_logs"],
    }
