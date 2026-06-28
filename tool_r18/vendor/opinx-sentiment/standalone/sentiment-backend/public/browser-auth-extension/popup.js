const $ = (id) => document.getElementById(id);

function setStatus(message) {
  $("status").textContent = message;
}

function send(message) {
  return chrome.runtime.sendMessage(message);
}

async function loadState() {
  const values = await chrome.storage.local.get(["apiBase", "lastStatus"]);
  $("apiBase").value = values.apiBase || "http://43.167.237.120";
  setStatus(values.lastStatus || "等待授权");
}

$("saveApi").addEventListener("click", async () => {
  const apiBase = $("apiBase").value.trim();
  const result = await send({ type: "set-api-base", apiBase });
  setStatus(result.ok ? `已保存：${result.apiBase}` : result.error);
});

$("openAuth").addEventListener("click", async () => {
  const result = await send({ type: "open-auth-pages" });
  setStatus(result.ok ? "已打开授权页面，请逐个登录" : result.error);
});

$("syncCurrent").addEventListener("click", async () => {
  const result = await send({ type: "sync-current-tab" });
  setStatus(result.ok ? "当前站点 Cookie 已同步" : result.error);
});

loadState().catch(error => setStatus(error.message));
