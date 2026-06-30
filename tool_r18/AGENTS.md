# AGENTS.md — Codex Agent 配置

本项目是一个**自动化推文运营控制台**，以后台 daemon + skill 脚本的方式运行。

## 启动后台服务

```bash
npm start
```

启动后会自动运行发布排程器，每 10 秒轮询队列并执行到期任务。

## 可用 Skill（Codex 可直接调用）

### 1. 生成人设推文

```bash
npm run skill:generate-persona -- '<JSON>'
```

输入格式：
```json
{
  "setup": {
    "genres": ["單身貴族"],
    "personaPersonality": "知性優雅",
    "personaGender": "女性",
    "personaStyle": "故事化表達",
    "totalEpisodes": 50,
    "targetMarket": "cn",
    "chineseScript": "traditional"
  },
  "personaContent": "人设档案内容",
  "count": 3,
  "dryRun": false
}
```

### 2. 生成人设图片

```bash
npm run skill:generate-persona-images -- '<JSON>'
```

输入格式：
```json
{
  "setup": { "...同上 setup...", "imageWorkflow": { "provider": "comfyui", "workflowFile": "人设1 金君雅.json", "workflowGroup": "批量文生圖", "personaKey": "jinjunya" } },
  "content": "推文内容",
  "model": "gemini-3.1-flash-image-preview",
  "aspectRatio": "1:1"
}
```

### 3. 发布到社交平台

```bash
npm run skill:publish-once -- '<JSON>'
```

输入格式：
```json
{
  "padCode": "ACP250801768QX47",
  "platform": "threads",
  "caption": "推文内容",
  "mediaUrl": "可选，图片/视频 URL 或 data URL",
  "dryRun": false
}
```

支持平台：`threads` / `instagram` / `twitter`
新增支持：`rednote`（小红书），无 `mediaUrl` 时会自动生成正常内容卡片再发布。

### 4. 记忆操作

```bash
npm run skill:memory -- '<JSON>'
```

输入格式：
```json
{ "action": "outline", "text": "要摘要的文本" }
{ "action": "thumbnail", "text": "要缩略的文本" }
{ "action": "format", "entries": [...], "limit": 12 }
```

### 5. 发布队列管理

```bash
npm run skill:publish-queue -- '<JSON>'
```

输入格式：
```json
{ "action": "enqueue", "task": { "pad_code": "ACP...", "platform": "threads", "caption": "..." } }
{ "action": "list", "status": "pending" }
```

### 6. 端到端验证

```bash
npm run skill:verify-path -- '<JSON>'
```

## 配置

### VMOS 智能體手機凭据

设置方式（任选一种）：
- 环境变量：`VMOS_AK` / `VMOS_SK`
- 文件：`electron/vmos-credentials.local.json`（格式：`{"ak":"...","sk":"..."}`）
- 多账号文件：`electron/vmos-credentials.local.json` 可使用 `{"accounts":[{"name":"primary","ak":"...","sk":"..."},{"name":"secondary","ak":"...","sk":"..."}]}`，程序会合并智能體手機列表并按 `padCode` 自动选择对应账号。

### ComfyUI 工作流

环境变量（有默认值）：
- `PERSONA_WORKFLOW_JUPYTER_BASE`
- `PERSONA_WORKFLOW_COMFY_BASE`
- `PERSONA_WORKFLOW_TOKEN`
- `PERSONA_WORKFLOW_LOCAL_DIR`

也可在 `.runtime/automatic-script/api_config.json` 中配置：
- `personaWorkflowJupyterBase` / `comfyWorkflowJupyterBase`
- `personaWorkflowComfyBase` / `comfyWorkflowComfyBase`
- `personaWorkflowToken` / `comfyWorkflowToken`
- `personaWorkflowLocalDir` / `comfyWorkflowLocalDir`
- `personaWorkflowGatewayToken` / `comfyWorkflowGatewayToken`（远端 gateway 需要鉴权时）
- `personaWorkflowAuthHeader` + `personaWorkflowAuthValue`（远端 gateway 使用自定义请求头时）

### AI 图片 API

在 `.runtime/automatic-script/api_config.json` 中配置：
```json
{
  "geminiKey": "your-key",
  "geminiEndpoint": "https://generativelanguage.googleapis.com"
}
```

## 架构

```
npm start                → 后台 daemon（排程 + 自动发布）
npm run skill:*          → Codex 调用的独立 skill 脚本
src/core/                → 纯业务逻辑
src/runtime/node/        → Node 运行时适配
src/lib/                 → 领域模块（发布、图片、记忆等）
```
