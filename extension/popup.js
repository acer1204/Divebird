const $ = (id) => document.getElementById(id);
const TAGS = { video: "MP4", hls: "HLS", dash: "DASH", audio: "音訊" };
let tab = null;

function humanSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function label(url) {
  try {
    const u = new URL(url);
    return decodeURIComponent(u.pathname.split("/").filter(Boolean).pop() || u.hostname);
  } catch {
    return url;
  }
}

function setMsg(text, ok) {
  $("msg").textContent = text;
  $("msg").className = ok ? "ok" : "err";
}

async function download(item) {
  setMsg("傳送中…", true);
  const res = await chrome.runtime.sendMessage({
    type: "download",
    tabId: tab.id,
    item: { ...item, referer: (item.headers && item.headers.Referer) || tab.url, source: "popup" },
  });
  if (res && res.ok) setMsg("✓ 已傳送到 Divebird", true);
  else setMsg((res && res.error) || "傳送失敗", false);
}

async function refreshStatus() {
  const res = await chrome.runtime.sendMessage({ type: "ping" });
  $("dot").className = `dot ${res && res.ok ? "ok" : "err"}`;
  $("status").textContent = res && res.ok ? "已連線" : "未連線（請啟動 Divebird）";
}

async function init() {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const s = await chrome.storage.local.get({ port: 17890, floatingButton: true, interceptDownloads: true });
  $("port").value = s.port;
  $("floatingButton").checked = s.floatingButton;
  $("interceptDownloads").checked = s.interceptDownloads;
  for (const id of ["floatingButton", "interceptDownloads"]) {
    $(id).addEventListener("change", () => chrome.storage.local.set({ [id]: $(id).checked }));
  }
  $("port").addEventListener("change", async () => {
    const p = Number($("port").value);
    if (p >= 1024 && p <= 65535) {
      await chrome.storage.local.set({ port: p });
      refreshStatus();
    }
  });

  refreshStatus();

  const isWeb = tab && /^https?:/i.test(tab.url || "");
  $("page-btn").disabled = !isWeb;
  $("page-btn").addEventListener("click", () => download({ url: tab.url, type: "page" }));

  const res = isWeb ? await chrome.runtime.sendMessage({ type: "getMedia", tabId: tab.id }) : { media: [] };
  const media = (res && res.media) || [];
  const list = $("list");
  $("media-title").textContent = `此分頁偵測到的影音（${media.length}）`;
  if (!media.length) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = isWeb
      ? "尚未偵測到影音檔或串流。請先播放影片；YouTube 等影音網站可直接用下方按鈕解析。"
      : "此頁面無法使用。";
    list.appendChild(d);
  }
  for (const m of media.slice().reverse()) {
    const row = document.createElement("div");
    row.className = "item";
    row.title = m.url;
    const tag = document.createElement("span");
    tag.className = `tag ${m.type}`;
    tag.textContent = TAGS[m.type] || m.type;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = label(m.url);
    const size = document.createElement("span");
    size.className = "size";
    size.textContent = m.type === "hls" || m.type === "dash" ? "串流" : humanSize(m.size);
    const b = document.createElement("button");
    b.textContent = "下載";
    b.addEventListener("click", () => download(m));
    row.append(tag, name, size, b);
    list.appendChild(row);
  }
}

init();
