# Live 联调冒烟集

本目录存放真实本地 ComfyUI + Modal Runner 的发布前验收用例，统一标记为
`@pytest.mark.live`。默认 `pytest` 不会执行它们，避免无凭证环境失败或产生费用。

## 运行前准备

1. 本地 ComfyUI 已按正常方式启动，且桥接器健康检查显示 `local.online=true`。
2. Modal Runner 已部署，`~/.comfybridge/config.toml` 或设置页已配置：
   - Modal 账户凭证；
   - 代理鉴权凭证 `proxy_token_id/proxy_token_secret`；
   - `web_url`。
3. 准备一个当前两端都可用的小型文生图工作流 JSON。
   - 文件格式必须是 ComfyUI API 工作流对象，即顶层含 `prompt` 字典；
   - 推荐使用低分辨率、少步数，先只验证通路。

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `CB_LIVE=1` | 总开关，未设置时全部 skip |
| `CB_BRIDGE_URL` | 桥接器地址，默认 `http://127.0.0.1:8189` |
| `CB_LIVE_MODAL=1` | 允许提交云端生成，会产生真实 Modal 费用 |
| `CB_LIVE_FIXTURE_JSON` | 自定义 API 工作流 JSON 路径 |
| `CB_LIVE_LOCAL_FIXTURE_JSON` | 只在本地可运行的工作流 JSON 路径（本地出图用例） |
| `CB_LIVE_INPUT_IMAGE` | 图生图输入文件名（相对本地 ComfyUI `input/`） |
| `CB_LIVE_NO_OUTPUT_FIXTURE_JSON` | 无输出工作流 JSON 路径 |
| `CB_LIVE_PROMPT_ID` | 可选，只读复验已有请求，不提交新任务 |

## 执行

先跑不产生费用的两端预检：

```powershell
cd backend
$env:CB_LIVE = "1"
.venv\Scripts\python.exe -m pytest -m live -q
```

确认预检通过后，再显式允许云端付费用例：

```powershell
$env:CB_LIVE_MODAL = "1"
$env:CB_LIVE_FIXTURE_JSON = "C:/path/to/txt2img-api.json"
.venv\Scripts\python.exe -m pytest -m live -q
```

只复验一条历史请求：

```powershell
$env:CB_LIVE_PROMPT_ID = "<prompt_id>"
.venv\Scripts\python.exe -m pytest -m live -q
```

## 用例边界

- 当前已落地：桥接健康检查、本地上游 `/object_info`、Modal 通道状态、历史请求只读复验、云端文生图全链路。
- 已落地：本地通道真实出图（终态不依赖浏览器 WS）、图生图输入推送、运行中中断、断线重连兜底、无输出工作流。
- 图生图与无输出用例需要专门夹具；未提供时 skip，不用删节点的方式伪造覆盖。
- 这些用例不写入仓库任何凭证或用户工作流；输出只作为运行时期望，不提交到仓库。
