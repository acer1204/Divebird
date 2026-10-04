// Divebird 內容腳本：在網頁影片上顯示懸浮的「下載此影片」按鈕
(() => {
  if (window.__divebirdLoaded) return;
  window.__divebirdLoaded = true;

  const MIN_W = 200;
  const MIN_H = 110;
  let enabled = true;
  let current = null;      // 目前按鈕所屬的 <video>
  let hideTimer = 0;
  let menuOpen = false;
  let videosCache = [];
  let videosDirty = true;

  chrome.storage.local.get({ floatingButton: true }).then((s) => (enabled = s.floatingButton));
  chrome.storage.onChanged.addListener((ch) => {
    if (ch.floatingButton) {
      enabled = ch.floatingButton.newValue;
      if (!enabled) hide(true);
    }
  });

  // ---------------------------------------------------------------- UI（closed shadow DOM，不受網頁 CSS 影響）
  const host = document.createElement("divebird-ui");
  host.style.cssText =
    "all:initial !important;position:fixed !important;top:0 !important;left:0 !important;" +
    "width:0 !important;height:0 !important;z-index:2147483647 !important;display:block !important;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
<style>
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { position: fixed; display: none; font: 13px/1.4 system-ui, -apple-system, "Segoe UI",
          "Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC", sans-serif; color: #e5e7eb;
          pointer-events: auto; }
  .wrap.show { display: block; animation: pop .14s ease-out; }
  @keyframes pop { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
  .btn { display: flex; align-items: center; gap: 6px; padding: 7px 13px 7px 10px; border: 0;
         border-radius: 999px; background: rgba(37, 99, 235, .94); color: #fff; font: inherit;
         font-weight: 600; cursor: pointer; box-shadow: 0 4px 16px rgba(0,0,0,.35);
         transition: background .12s, transform .12s; white-space: nowrap; margin-left: auto; }
  .btn:hover { background: #1d4ed8; transform: translateY(-1px); }
  .btn svg { width: 17px; height: 17px; flex: none; }
  .btn.busy { opacity: .75; cursor: progress; }
  .menu { position: absolute; right: 0; top: calc(100% + 8px); width: 380px; max-width: 80vw;
          background: rgba(17, 24, 39, .97); border: 1px solid rgba(255,255,255,.08); border-radius: 12px;
          padding: 6px; box-shadow: 0 14px 40px rgba(0,0,0,.5); display: none; }
  .menu.show { display: block; }
  .menu h4 { margin: 4px 8px 6px; font-size: 12px; font-weight: 600; color: #9ca3af; }
  .item { display: flex; align-items: center; gap: 9px; padding: 8px; border-radius: 8px; cursor: pointer; }
  .item:hover { background: rgba(255,255,255,.08); }
  .tag { flex: none; min-width: 44px; text-align: center; font-size: 11px; font-weight: 700; color: #fff;
         padding: 2px 6px; border-radius: 5px; background: #2563eb; letter-spacing: .3px; }
  .tag.hls { background: #7c3aed; } .tag.dash { background: #db2777; }
  .tag.audio { background: #059669; } .tag.page { background: #475569; }
  .name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .size { flex: none; color: #9ca3af; font-size: 12px; }
  .toast { position: absolute; right: 0; top: calc(100% + 8px); width: max-content; max-width: 340px; padding: 9px 12px;
           border-radius: 10px; background: rgba(17,24,39,.96); box-shadow: 0 10px 30px rgba(0,0,0,.45);
           display: none; white-space: normal; }
  .toast.show { display: block; }
  .toast.ok { border-left: 4px solid #22c55e; } .toast.err { border-left: 4px solid #ef4444; }
</style>
<div class="wrap" part="wrap">
  <button class="btn" type="button" title="用 Divebird 下載此影片">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"
      stroke-linejoin="round"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>
    <span>下載此影片</span>
  </button>
  <div class="menu"></div>
  <div class="toast"></div>
</div>`;
  const wrap = root.querySelector(".wrap");
  const btn = root.querySelector(".btn");
  const menu = root.querySelector(".menu");
  const toast = root.querySelector(".toast");

  function mount() {
    const fs = document.fullscreenElement;
    // 全螢幕時要放進全螢幕元素裡才看得到（若全螢幕的是 <video> 本身則無法顯示）
    const parent = fs && fs.tagName !== "VIDEO" ? fs : document.documentElement;
    if (host.parentNode !== parent) parent.appendChild(host);
  }

  // ---------------------------------------------------------------- 找出頁面上的影片
  let shadowHosts = [];
  let lastShadowScan = -1e9;

  function collectVideos() {
    if (!videosDirty) return videosCache;
    // 開放式 shadow DOM 裡的播放器（部分 Web Components 播放器）：
    // 完整掃描 DOM 較耗時，最多每 2 秒一次
    const now = performance.now();
    if (now - lastShadowScan > 2000) {
      lastShadowScan = now;
      shadowHosts = [];
      for (const el of document.querySelectorAll("*")) if (el.shadowRoot) shadowHosts.push(el);
    }
    const found = [...document.querySelectorAll("video")];
    for (const h of shadowHosts) if (h.shadowRoot) found.push(...h.shadowRoot.querySelectorAll("video"));
    videosCache = found;
    videosDirty = false;
    return found;
  }
  new MutationObserver(() => (videosDirty = true)).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });

  function usable(v) {
    if (!v.isConnected) return false;
    const r = v.getBoundingClientRect();
    if (r.width < MIN_W || r.height < MIN_H) return false;
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
    const st = getComputedStyle(v);
    return st.visibility !== "hidden" && st.display !== "none" && Number(st.opacity) > 0.05;
  }

  function videoAt(x, y) {
    for (const v of collectVideos()) {
      if (!usable(v)) continue;
      const r = v.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return v;
    }
    return null;
  }

  // ---------------------------------------------------------------- 顯示 / 隱藏 / 定位
  function place() {
    if (!current) return;
    const r = current.getBoundingClientRect();
    const top = Math.max(r.top, 0) + 12;
    const right = Math.max(innerWidth - Math.min(r.right, innerWidth), 0) + 12;
    wrap.style.top = `${top}px`;
    wrap.style.right = `${right}px`;
    wrap.style.left = "auto";
  }

  function show(v) {
    if (!enabled) return;
    clearTimeout(hideTimer);
    if (current !== v) closeMenu();
    current = v;
    mount();
    place();
    if (!wrap.classList.contains("show")) wrap.classList.add("show");
  }

  function hide(force) {
    if (!force && (menuOpen || btn.classList.contains("busy"))) return;
    wrap.classList.remove("show");
    closeMenu();
    current = null;
  }

  function scheduleHide(ms = 1200) {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => hide(false), ms);
  }

  let lastMove = 0;
  document.addEventListener(
    "mousemove",
    (e) => {
      if (!enabled) return;
      const now = performance.now();
      if (now - lastMove < 80) return;
      lastMove = now;
      if (e.composedPath().includes(host)) {
        clearTimeout(hideTimer);
        return;
      }
      const v = videoAt(e.clientX, e.clientY);
      if (v) show(v);
      else if (current) scheduleHide();
    },
    { capture: true, passive: true }
  );

  document.addEventListener(
    "play",
    (e) => {
      const v = e.target;
      if (v instanceof HTMLVideoElement && usable(v)) {
        show(v);
        scheduleHide(3500);
      }
    },
    true
  );

  addEventListener(
    "scroll",
    () => {
      if (!current) return;
      if (usable(current)) place();
      else hide(true); // 影片被捲出畫面
    },
    { capture: true, passive: true }
  );
  addEventListener("resize", () => current && place(), { passive: true });
  document.addEventListener("fullscreenchange", () => {
    mount();
    if (current) place();
  });
  wrap.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  wrap.addEventListener("mouseleave", () => scheduleHide(menuOpen ? 2500 : 1200));
  document.addEventListener(
    "mousedown",
    (e) => {
      if (menuOpen && !e.composedPath().includes(host)) closeMenu();
    },
    true
  );

  // Alt + 點擊連結：這次下載不要攔截，交給瀏覽器
  document.addEventListener(
    "click",
    (e) => {
      if (e.altKey) safeSend({ type: "bypassNext" });
    },
    true
  );

  // ---------------------------------------------------------------- 下載流程
  async function safeSend(msg) {
    try {
      return await chrome.runtime.sendMessage(msg);
    } catch (e) {
      return { ok: false, error: "擴充功能已更新，請重新整理此頁面" };
    }
  }

  function fileLabel(url) {
    try {
      const u = new URL(url);
      const last = decodeURIComponent(u.pathname.split("/").filter(Boolean).pop() || u.hostname);
      return last.length > 60 ? last.slice(0, 28) + "…" + last.slice(-28) : last;
    } catch {
      return url;
    }
  }

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

  async function candidates(v) {
    const list = [];
    const seen = new Set();
    const add = (item) => {
      if (!seen.has(item.url)) {
        seen.add(item.url);
        list.push(item);
      }
    };
    const srcs = [v.currentSrc, v.src, ...[...v.querySelectorAll("source")].map((s) => s.src)];
    for (const s of srcs) if (s && /^https?:/i.test(s)) add({ url: s, type: "video", label: fileLabel(s) });

    const res = (await safeSend({ type: "getMedia" })) || {};
    const order = { video: 0, hls: 1, dash: 2, audio: 3 };
    const sniffed = (res.media || []).slice().sort((a, b) => (order[a.type] ?? 9) - (order[b.type] ?? 9));
    for (const m of sniffed) add({ ...m, label: fileLabel(m.url) });

    const media = list.slice();
    const pageUrl = location.href;
    add({ url: pageUrl, type: "page", label: "以 yt-dlp 解析此網頁（影音網站建議用此項）" });
    if (window.top !== window && res.tabUrl && res.tabUrl !== pageUrl) {
      add({ url: res.tabUrl, type: "page", label: "解析上層網頁" });
    }
    return { media, all: list };
  }

  const TAGS = { video: "MP4", hls: "HLS", dash: "DASH", audio: "音訊", page: "網頁" };

  function renderMenu(items) {
    menu.textContent = "";
    const h = document.createElement("h4");
    h.textContent = "選擇要用 Divebird 下載的項目";
    menu.appendChild(h);
    for (const it of items) {
      const row = document.createElement("div");
      row.className = "item";
      row.title = it.url;
      const tag = document.createElement("span");
      tag.className = `tag ${it.type}`;
      let tagText = TAGS[it.type] || it.type;
      if (it.type === "video" && it.mime && it.mime.includes("webm")) tagText = "WEBM";
      tag.textContent = tagText;
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = it.label;
      const size = document.createElement("span");
      size.className = "size";
      // m3u8 / mpd 的大小只是播放清單本身，不顯示
      size.textContent = it.type === "hls" || it.type === "dash" ? "串流" : humanSize(it.size);
      row.append(tag, name, size);
      row.addEventListener("click", (e) => {
        e.stopPropagation();
        closeMenu();
        submit(it);
      });
      menu.appendChild(row);
    }
    menu.classList.add("show");
    menuOpen = true;
    toast.classList.remove("show");
  }

  function closeMenu() {
    menu.classList.remove("show");
    menuOpen = false;
  }

  function showToast(text, ok) {
    toast.textContent = text;
    toast.className = `toast show ${ok ? "ok" : "err"}`;
    clearTimeout(showToast.t);
    showToast.t = setTimeout(() => {
      toast.classList.remove("show");
      scheduleHide(800);
    }, ok ? 2200 : 4500);
  }

  async function submit(item) {
    btn.classList.add("busy");
    const payload = {
      url: item.url,
      type: item.type,
      headers: item.headers || {},
      referer: (item.headers && (item.headers.Referer || item.headers.referer)) || location.href,
      pageUrl: window.top === window ? location.href : undefined,
      title: window.top === window ? document.title : undefined,
      source: "floating-button",
    };
    const res = await safeSend({ type: "download", item: payload });
    btn.classList.remove("busy");
    if (res && res.ok) showToast("✓ 已傳送到 Divebird", true);
    else showToast((res && res.error) || "傳送失敗", false);
  }

  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!current) return;
    if (menuOpen) return closeMenu();
    btn.classList.add("busy");
    const { media, all } = await candidates(current);
    btn.classList.remove("busy");
    // 沒偵測到檔案 / 串流（例如 YouTube）→ 直接整頁交給 yt-dlp；只有一個 → 直接下載；多個 → 讓使用者選
    if (media.length === 0) submit(all[0]);
    else if (media.length === 1 && all.length <= 2 && media[0].type !== "audio") submit(media[0]);
    else renderMenu(all);
  });

  // 背景傳來的提示（例如右鍵選單下載失敗）
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "toast" && window.top === window) {
      const v = collectVideos().find(usable);
      if (v) show(v);
      else {
        mount();
        wrap.style.top = "16px";
        wrap.style.right = "16px";
        wrap.classList.add("show");
      }
      showToast(msg.message, !msg.error);
    }
  });
})();
