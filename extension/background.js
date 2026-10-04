// Divebird 擴充功能背景服務（Manifest V3 service worker）
//  1. 監聽網路請求，偵測每個分頁中的影音檔與 HLS(m3u8) / DASH(mpd) 串流
//  2. 接收懸浮按鈕 / 彈出視窗 / 右鍵選單的下載請求，連同 Cookie、Referer 傳給桌面程式
//  3. （可選）攔截瀏覽器的一般下載，改交給 Divebird 多連線下載

const DEFAULTS = {
  port: 17890,
  floatingButton: true,
  interceptDownloads: true,
  minInterceptKB: 0,
};
const MAX_ITEMS_PER_TAB = 60;
const MIN_MEDIA_BYTES = 300 * 1024; // 小於此大小的影音回應多半是片段或廣告，忽略
const IGNORE_HOSTS = [
  /(^|\.)googlevideo\.com$/,   // YouTube 的 DASH 分片（整頁交給 yt-dlp 解析）
  /(^|\.)doubleclick\.net$/,
  /(^|\.)googlesyndication\.com$/,
  /(^|\.)imasdk\.googleapis\.com$/,
];

async function getSettings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

// ------------------------------------------------------------------ 媒體偵測
const tabMedia = new Map();       // tabId -> 偵測到的媒體陣列
const pendingHeaders = new Map(); // requestId -> 請求標頭（Referer / Origin / Authorization…）
const tabUrls = new Map();        // tabId -> 目前頁面網址（用來判斷換頁）

function classify(url, contentType) {
  let path;
  try {
    path = new URL(url).pathname.toLowerCase();
  } catch {
    return null;
  }
  const ct = (contentType || "").split(";")[0].trim().toLowerCase();
  if (ct.includes("mpegurl") || path.endsWith(".m3u8")) return "hls";
  if (ct === "application/dash+xml" || path.endsWith(".mpd")) return "dash";
  // HLS / DASH 的分段檔不列出
  if (/\.(ts|m4s|cmfv|cmfa|m4f|aac)$/.test(path) || ct === "video/mp2t" || ct === "video/iso.segment") return null;
  if (ct.startsWith("video/")) return "video";
  if (ct.startsWith("audio/")) return "audio";
  if (/\.(mp4|webm|mkv|flv|mov|m4v|avi|wmv)$/.test(path)) return "video";
  if (/\.(mp3|m4a|ogg|oga|opus|flac|wav)$/.test(path)) return "audio";
  return null;
}

function headerValue(headers, name) {
  const h = (headers || []).find((x) => x.name.toLowerCase() === name);
  return h ? h.value : "";
}

function totalSize(headers) {
  const cr = headerValue(headers, "content-range");
  const m = /\/(\d+)\s*$/.exec(cr);
  if (m) return Number(m[1]);
  const cl = headerValue(headers, "content-length");
  return cl ? Number(cl) : 0;
}

function mediaKey(url) {
  try {
    const u = new URL(url);
    return u.origin + u.pathname; // 同一檔案的不同 Range / token 參數視為同一項
  } catch {
    return url;
  }
}

async function loadTab(tabId) {
  if (tabMedia.has(tabId)) return tabMedia.get(tabId);
  // service worker 可能被瀏覽器回收，從 session storage 還原
  const key = `media_${tabId}`;
  const saved = (await chrome.storage.session.get(key))[key] || [];
  if (!tabMedia.has(tabId)) tabMedia.set(tabId, saved);
  return tabMedia.get(tabId);
}

const saveTimers = new Map();
function persistTab(tabId) {
  clearTimeout(saveTimers.get(tabId));
  saveTimers.set(
    tabId,
    setTimeout(() => {
      const list = tabMedia.get(tabId);
      const key = `media_${tabId}`;
      if (list && list.length) chrome.storage.session.set({ [key]: list });
      else chrome.storage.session.remove(key);
    }, 300)
  );
}

function updateBadge(tabId) {
  const n = (tabMedia.get(tabId) || []).length;
  chrome.action.setBadgeText({ tabId, text: n ? String(n) : "" }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#2563eb" }).catch(() => {});
}

async function clearTab(tabId) {
  tabMedia.set(tabId, []);
  persistTab(tabId);
  updateBadge(tabId);
}

chrome.webRequest.onBeforeSendHeaders.addListener(
  (d) => {
    if (d.tabId < 0) return;
    const keep = {};
    for (const h of d.requestHeaders || []) {
      const n = h.name.toLowerCase();
      if (n === "referer" || n === "origin" || n === "authorization" || n.startsWith("x-")) keep[h.name] = h.value;
    }
    pendingHeaders.set(d.requestId, keep);
    if (pendingHeaders.size > 2000) pendingHeaders.delete(pendingHeaders.keys().next().value);
  },
  { urls: ["http://*/*", "https://*/*"], types: ["media", "xmlhttprequest", "other", "object"] },
  ["requestHeaders", "extraHeaders"]
);

chrome.webRequest.onHeadersReceived.addListener(
  (d) => {
    const reqHeaders = pendingHeaders.get(d.requestId) || {};
    pendingHeaders.delete(d.requestId);
    if (d.tabId < 0 || d.statusCode >= 400) return;
    let host;
    try {
      host = new URL(d.url).hostname;
    } catch {
      return;
    }
    if (IGNORE_HOSTS.some((re) => re.test(host))) return;
    const contentType = headerValue(d.responseHeaders, "content-type");
    const type = classify(d.url, contentType);
    if (!type) return;
    const size = totalSize(d.responseHeaders);
    if ((type === "video" || type === "audio") && size > 0 && size < MIN_MEDIA_BYTES) return;
    recordMedia(d.tabId, {
      url: d.url,
      type,
      mime: contentType.split(";")[0].trim(),
      size,
      headers: reqHeaders,
      time: Date.now(),
    });
  },
  { urls: ["http://*/*", "https://*/*"], types: ["media", "xmlhttprequest", "other", "object"] },
  ["responseHeaders"]
);

chrome.webRequest.onErrorOccurred.addListener((d) => pendingHeaders.delete(d.requestId), {
  urls: ["http://*/*", "https://*/*"],
});

async function recordMedia(tabId, item) {
  const list = await loadTab(tabId);
  const key = mediaKey(item.url);
  const idx = list.findIndex((m) => mediaKey(m.url) === key);
  if (idx >= 0) {
    const old = list[idx];
    list[idx] = { ...old, url: item.url, size: Math.max(old.size || 0, item.size || 0), headers: item.headers };
  } else {
    list.push(item);
    if (list.length > MAX_ITEMS_PER_TAB) list.shift();
  }
  persistTab(tabId);
  updateBadge(tabId);
}

// 換頁（包含 YouTube 這類單頁應用切換影片）時清除舊的偵測結果
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (!info.url) return;
  const strip = (u) => u.split("#")[0];
  const key = `url_${tabId}`;
  let prev = tabUrls.get(tabId);
  // service worker 被回收後記憶體中的網址會消失：從 session storage 取回，才判斷得出是否換頁
  if (prev === undefined) prev = (await chrome.storage.session.get(key))[key];
  tabUrls.set(tabId, info.url);
  chrome.storage.session.set({ [key]: info.url });
  if (prev !== undefined && strip(prev) !== strip(info.url)) clearTab(tabId);
});
chrome.tabs.onRemoved.addListener((tabId) => {
  tabMedia.delete(tabId);
  tabUrls.delete(tabId);
  chrome.storage.session.remove([`media_${tabId}`, `url_${tabId}`]);
});

// ------------------------------------------------------------------ 與桌面程式通訊
async function apiBase() {
  const { port } = await getSettings();
  return `http://127.0.0.1:${port}`;
}

async function ping() {
  try {
    const r = await fetch(`${await apiBase()}/api/ping`, { signal: AbortSignal.timeout(1500) });
    if (!r.ok) return false;
    const j = await r.json();
    return j.app === "Divebird";
  } catch {
    return false;
  }
}

async function collectCookies(urls) {
  const seen = new Map();
  for (const u of urls) {
    if (!/^https?:/i.test(u || "")) continue;
    try {
      for (const c of await chrome.cookies.getAll({ url: u })) {
        seen.set(`${c.domain}|${c.path}|${c.name}`, {
          name: c.name,
          value: c.value,
          domain: c.domain,
          path: c.path,
          secure: c.secure,
          httpOnly: c.httpOnly,
          hostOnly: c.hostOnly,
          expirationDate: c.expirationDate || 0,
        });
      }
    } catch {
      /* 某些網址無法讀取 Cookie，略過 */
    }
  }
  return [...seen.values()];
}

/**
 * 把下載交給 Divebird。
 * item: { url, type?: 'hls'|'dash'|'video'|'audio'|'page'|'link', kind?, headers?, filename?, referer? }
 */
async function sendToApp(item, tab) {
  const pageUrl = item.pageUrl || (tab && tab.url) || "";
  let kind = item.kind;
  if (!kind) {
    if (item.type === "hls" || item.type === "dash" || item.type === "page") kind = "media";
    else if (item.type === "video" || item.type === "audio") kind = "http";
    else kind = "auto";
  }
  const headers = { ...(item.headers || {}) };
  const referer = item.referer || headers.Referer || headers.referer || pageUrl;
  delete headers.Referer;
  delete headers.referer;
  const payload = {
    url: item.url,
    kind,
    page_url: pageUrl,
    referer,
    user_agent: navigator.userAgent,
    title: item.title || (tab && tab.title) || "",
    filename: item.filename || "",
    headers,
    cookies: await collectCookies([item.url, pageUrl]),
    source: item.source || "",
  };
  let r;
  try {
    r = await fetch(`${await apiBase()}/api/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    throw new Error("無法連線到 Divebird，請確認桌面程式已啟動");
  }
  if (!r.ok) {
    let msg = `Divebird 回應錯誤（HTTP ${r.status}）`;
    try {
      msg = (await r.json()).error || msg;
    } catch {}
    throw new Error(msg);
  }
}

// ------------------------------------------------------------------ 訊息
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg && msg.type) {
      case "getMedia": {
        const tabId = msg.tabId ?? sender.tab?.id;
        const tab = tabId != null ? await chrome.tabs.get(tabId).catch(() => null) : null;
        const media = tabId != null ? await loadTab(tabId) : [];
        return { media, tabUrl: tab?.url || "", tabTitle: tab?.title || "" };
      }
      case "download": {
        const tabId = msg.tabId ?? sender.tab?.id;
        const tab = tabId != null ? await chrome.tabs.get(tabId).catch(() => null) : null;
        await sendToApp(msg.item, tab);
        return { ok: true };
      }
      case "ping":
        return { ok: await ping() };
      case "bypassNext":
        bypassUntil = Date.now() + 4000;
        return { ok: true };
      default:
        return { ok: false, error: "unknown message" };
    }
  })().then(
    (res) => sendResponse(res),
    (err) => sendResponse({ ok: false, error: String(err && err.message ? err.message : err) })
  );
  return true; // 非同步回應
});

// ------------------------------------------------------------------ 右鍵選單
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "divebird-link", title: "用 Divebird 下載此連結", contexts: ["link"] });
    chrome.contextMenus.create({ id: "divebird-media", title: "用 Divebird 下載此影片／音訊", contexts: ["video", "audio"] });
    chrome.contextMenus.create({ id: "divebird-page", title: "用 Divebird 解析此頁面的影片", contexts: ["page", "frame"] });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let item;
  if (info.menuItemId === "divebird-link") item = { url: info.linkUrl, type: "link", source: "context-menu" };
  else if (info.menuItemId === "divebird-media") {
    item = /^https?:/i.test(info.srcUrl || "")
      ? { url: info.srcUrl, type: info.mediaType === "audio" ? "audio" : "video", source: "context-menu" }
      : { url: info.frameUrl || info.pageUrl, type: "page", source: "context-menu" }; // blob: 串流改為解析頁面
  } else if (info.menuItemId === "divebird-page") {
    item = { url: info.frameUrl || info.pageUrl, type: "page", source: "context-menu" };
  }
  if (!item) return;
  try {
    await sendToApp(item, tab);
  } catch (e) {
    notifyTab(tab, e.message);
  }
});

function notifyTab(tab, message) {
  if (tab && tab.id >= 0) chrome.tabs.sendMessage(tab.id, { type: "toast", message, error: true }).catch(() => {});
}

// ------------------------------------------------------------------ 攔截瀏覽器下載
let bypassUntil = 0;           // 使用者按住 Alt 點擊時，下一個下載不攔截
const bypassUrls = new Set();  // 交還給瀏覽器的下載，避免再次攔截

chrome.downloads.onCreated.addListener(async (item) => {
  const url = item.finalUrl || item.url;
  if (bypassUrls.has(url)) {
    bypassUrls.delete(url);
    return;
  }
  if (Date.now() < bypassUntil) {
    bypassUntil = 0;
    return;
  }
  if (!/^https?:/i.test(url) || item.state !== "in_progress" || item.byExtensionId) return;
  const s = await getSettings();
  if (!s.interceptDownloads) return;
  if (s.minInterceptKB > 0 && item.totalBytes > 0 && item.totalBytes < s.minInterceptKB * 1024) return;
  if (!(await ping())) return; // 桌面程式沒在執行 → 照常由瀏覽器下載

  try {
    await chrome.downloads.cancel(item.id);
    await chrome.downloads.erase({ id: item.id });
  } catch {
    return;
  }
  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    await sendToApp(
      { url, kind: "auto", referer: item.referrer, pageUrl: item.referrer, source: "intercept" },
      tabs[0]
    );
  } catch {
    bypassUrls.add(url);
    chrome.downloads.download({ url });
  }
});
