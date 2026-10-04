// ==UserScript==
// @name         Treasure Up · B站选片助手
// @namespace    treasure-up
// @version      1.0.0
// @description  在B站选中视频，批量加入自己的 Treasure Up 备份库。需要 Tampermonkey 5.0+。
// @author       Treasure Up
// @match        https://www.bilibili.com/*
// @match        https://search.bilibili.com/*
// @match        https://space.bilibili.com/*
// @match        https://m.bilibili.com/*
// @run-at       document-idle
// @noframes
// @sandbox      DOM
// @grant        GM.info
// @grant        GM.getValue
// @grant        GM.setValue
// @grant        GM.getTab
// @grant        GM.saveTab
// @grant        GM.xmlHttpRequest
// @grant        GM.registerMenuCommand
// @grant        window.onurlchange
// @connect      localhost
// @connect      127.0.0.1
// @connect      *
// ==/UserScript==

(() => {
  'use strict';

  // CORE-BEGIN: pure helpers and an injected GM client, tested directly from this file.
  const MAX_BATCH = 50;
  const BVID = /^BV[A-Za-z0-9]{10}$/;
  const TOKEN = /^tu_ingest_[A-Za-z0-9_-]{32,128}$/;
  function extractBvids(text) {
    const matches =
      String(text || '').match(/(?<![A-Za-z0-9])BV[A-Za-z0-9]{10}(?![A-Za-z0-9])/g) || [];
    return [...new Set(matches)];
  }
  function videoFromUrl(value, base = 'https://www.bilibili.com') {
    try {
      const url = new URL(value, base);
      if (
        !['http:', 'https:'].includes(url.protocol) ||
        !/(^|\.)bilibili\.com$/i.test(url.hostname)
      )
        return null;
      const path = url.pathname.match(/^\/video\/(BV[A-Za-z0-9]{10})(?:\/|$)/);
      const bvid = path?.[1] || url.searchParams.get('bvid');
      return BVID.test(bvid || '') ? bvid : null;
    } catch {
      return null;
    }
  }
  function privateAddress(hostname) {
    const host = hostname.toLowerCase().replace(/^\[|\]$/g, '');
    if (host === 'localhost' || host === '::1') return true;
    if (/^f[cd][0-9a-f]{2}:/i.test(host)) return true;
    const bytes = host.split('.').map(Number);
    if (bytes.length !== 4 || bytes.some((n) => !Number.isInteger(n) || n < 0 || n > 255))
      return false;
    return (
      bytes[0] === 127 ||
      bytes[0] === 10 ||
      (bytes[0] === 192 && bytes[1] === 168) ||
      (bytes[0] === 172 && bytes[1] >= 16 && bytes[1] <= 31)
    );
  }
  function normalizeBackend(value) {
    let url;
    try {
      url = new URL(String(value || '').trim());
    } catch {
      throw new Error('请填写完整服务地址，例如 https://archive.example.com');
    }
    if (url.username || url.password || url.search || url.hash)
      throw new Error('服务地址不能包含账号、密码、查询参数或片段。');
    if (!['/', '', '/api/v1', '/api/v1/'].includes(url.pathname))
      throw new Error('请填写站点根地址；支持 /api/v1 后缀，不支持其他子目录。');
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && privateAddress(url.hostname)))
      throw new Error('公网服务需要 HTTPS；HTTP 仅支持本机或明确的私网 IP 地址。');
    return url.origin;
  }
  function nextConfig(saved, backend, inputToken) {
    const origin = normalizeBackend(backend);
    const entered = String(inputToken || '').trim();
    if (origin !== saved.backend && !entered)
      throw new Error('更换服务地址后，请重新填写该服务的专用令牌。');
    const token = entered || saved.token || '';
    if (!TOKEN.test(token)) throw new Error('请填写后台「浏览器采集」创建的 tu_ingest_ 专用令牌。');
    return { backend: origin, token };
  }
  function supportedManager(info) {
    return (
      info?.scriptHandler === 'Tampermonkey' &&
      info.sandboxMode === 'dom' &&
      /^\d+\./.test(info.version || '') &&
      Number(info.version.split('.')[0]) >= 5
    );
  }
  function safeError(detail, token = '') {
    let text =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((item) => (typeof item?.msg === 'string' ? item.msg : '请求参数无效'))
              .join('；')
          : '请求失败，请在后台检查集成设置。';
    if (token) text = text.split(token).join('[令牌已隐藏]');
    return text.replace(/tu_ingest_[A-Za-z0-9_-]+/g, '[令牌已隐藏]').slice(0, 600);
  }
  function mergeSelection(current, candidates) {
    const result = new Map(current);
    let added = 0,
      overflow = 0;
    for (const candidate of candidates) {
      if (!BVID.test(candidate?.bvid || '') || result.has(candidate.bvid)) continue;
      if (result.size >= MAX_BATCH) {
        overflow++;
        continue;
      }
      result.set(candidate.bvid, {
        bvid: candidate.bvid,
        title: String(candidate.title || candidate.bvid).slice(0, 200),
      });
      added++;
    }
    return { items: result, added, overflow };
  }
  function pendingItems(items) {
    return [...items.values()].filter(
      (item) => !item.status || ['daily_limit', 'failed'].includes(item.status),
    );
  }
  function createDraftStore(gm, onUpdate) {
    let tail = Promise.resolve(),
      revision = 0;
    async function read() {
      const tab = await gm.getTab();
      const saved = tab?.treasureUpDraft;
      return {
        tab: tab || {},
        items: mergeSelection(new Map(), Array.isArray(saved) ? saved : []).items,
      };
    }
    async function refresh() {
      const current = ++revision,
        { items } = await read();
      if (current === revision) onUpdate(items);
    }
    function change(action) {
      revision++;
      const job = tail.then(async () => {
        const { tab, items: current } = await read();
        const next =
          action.type === 'add'
            ? mergeSelection(current, action.items)
            : { items: current, added: 0, overflow: 0 };
        if (action.type === 'remove') for (const bvid of action.bvids) next.items.delete(bvid);
        // Only BV and title are persisted. No credential, page URL, image URL or backend response enters this store.
        await gm.saveTab({
          ...tab,
          treasureUpDraft: [...next.items.values()].map(({ bvid, title }) => ({ bvid, title })),
        });
        return next;
      });
      tail = job.catch(() => {});
      return job.then(async (result) => {
        await refresh();
        return result;
      });
    }
    return { refresh, change };
  }
  function createClient(gm, config, timeoutMs = 20000) {
    // Do not silently fall back to fetch/XHR or another manager: ignored redirect options can leak Authorization.
    async function request(endpoint, bvids) {
      if (!supportedManager(gm.info))
        throw new Error('需要 Tampermonkey 5.0+ 的 DOM 隔离环境；请检查扩展沙盒设置。');
      const origin = normalizeBackend(config.backend);
      if (!TOKEN.test(config.token || '')) throw new Error('请先保存有效的专用令牌。');
      const url = `${origin}/api/v1/integrations/userscript/${endpoint}`;
      let timer, handle;
      try {
        handle = gm.xmlHttpRequest({
          method: bvids ? 'POST' : 'GET',
          url,
          headers: {
            Accept: 'application/json',
            Authorization: `Bearer ${config.token}`,
            ...(bvids ? { 'Content-Type': 'application/json' } : {}),
          },
          ...(bvids ? { data: JSON.stringify({ bvids }) } : {}),
          anonymous: true,
          redirect: 'error',
          fetch: true,
          nocache: true,
          responseType: 'json',
        });
        const response = await Promise.race([
          Promise.resolve(handle).catch(() => {
            throw new Error(
              '连接失败。请检查地址、扩展访问权限与网络；服务必须直接响应，不能重定向。',
            );
          }),
          new Promise((_, reject) => {
            timer = setTimeout(() => {
              reject(
                new Error(
                  bvids
                    ? '连接超时。任务可能已提交；重试会复用已有任务。'
                    : '连接超时，请检查服务地址与网络。',
                ),
              );
              try {
                handle?.abort?.();
              } catch {
                /* Timeout is already reported without exposing request details. */
              }
            }, timeoutMs);
          }),
        ]);
        // Defence in depth only. redirect:error prevents transmission to a redirect target before this check.
        if (
          !response.finalUrl ||
          response.finalUrl !== url ||
          (response.status >= 300 && response.status < 400)
        )
          throw new Error('服务发生重定向，已拒绝此响应。请配置最终服务地址。');
        let body = response.response;
        if (!body || typeof body !== 'object') {
          try {
            body = JSON.parse(String(response.responseText || '').slice(0, 100000));
          } catch {
            throw new Error('服务没有返回有效 JSON，请确认这是 Treasure Up 的站点地址。');
          }
        }
        if (response.status < 200 || response.status >= 300)
          throw new Error(`HTTP ${response.status} · ${safeError(body.detail, config.token)}`);
        return body;
      } finally {
        clearTimeout(timer);
      }
    }
    return {
      async status() {
        const result = await request('status');
        if (result.ok !== true || result.scope !== 'ingest.submit')
          throw new Error('连接响应不符合选片助手协议，请检查服务版本。');
        return result;
      },
      submit(bvids) {
        const unique = [...new Set(bvids)];
        if (
          !unique.length ||
          unique.length > MAX_BATCH ||
          unique.some((value) => !BVID.test(value))
        )
          throw new Error('每批只能提交 1–50 个有效 BV 号。');
        return request('videos', unique);
      },
    };
  }
  // CORE-END

  if (typeof GM === 'undefined' || !document.body) return;
  const STORAGE = 'treasure-up:collector:v1';
  const CARD =
    '.bili-video-card, .video-card, .video-item, .small-item, .video-list-item, .vui_video_item, .vui_video-card, .fav-video-list > li, .bili-video-card__wrap';
  const LINKS = 'a[href*="/video/BV"], a[href*="bvid=BV"], [data-bvid]';
  const icons = {
    library: 'M4 5h16v14H4zM8 9l7 3-7 3V9Z',
    check: 'm5 12 4 4L19 6',
    plus: 'M12 5v14M5 12h14',
    close: 'm6 6 12 12M6 18 18 6',
    settings: 'M4 7h16M4 17h16M9 4v6M15 14v6',
    select: 'M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5m-6-8 2 2 4-4',
    paste: 'M9 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3M9 3h6v4H9z',
    link: 'm9 15 6-6M8 16l-2 2a3 3 0 0 1-4-4l5-5a3 3 0 0 1 4 0m2 6a3 3 0 0 0 4 0l5-5a3 3 0 0 0-4-4l-2 2',
    send: 'm3 11 18-8-8 18-2-8-8-2Zm8 2L21 3',
    trash: 'M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 10v7m4-7v7',
    chevron: 'm6 9 6 6 6-6',
    video: 'M4 5h16v14H4zM10 9l5 3-5 3V9Z',
    info: 'M12 8h.01M12 11v6M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z',
  };
  const icon = (name) =>
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${icons[name] || icons.video}"/></svg>`;
  const host = document.createElement('div');
  host.setAttribute('data-treasure-up', 'collector');
  host.style.cssText = 'all:initial;position:fixed;inset:0;z-index:2147483000;pointer-events:none;';
  const shadow = host.attachShadow({ mode: 'closed' });
  // All HTML is constant. Page titles, API messages and identifiers are inserted with textContent below.
  shadow.innerHTML = `<style>
    :host{font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;color:#203b36;font-size:13px;line-height:1.5}
    *,*:before,*:after{box-sizing:border-box}button,input,textarea{font:inherit}button{cursor:pointer}button:disabled{opacity:.45;cursor:default}svg{width:19px;height:19px;flex-shrink:0}button{border:0;color:inherit}button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:2px solid #16847a;outline-offset:3px}[hidden]{display:none!important}
    .fab{pointer-events:auto;position:fixed;right:24px;bottom:28px;display:flex;gap:8px;align-items:center;padding:12px 16px;border-radius:999px;background:#126f66;color:#fff;box-shadow:0 5px 20px #123e3a30;font-weight:600;letter-spacing:.3px}.fab:hover{background:#0e5e57}.fab-count{min-width:20px;padding:0 5px;border-radius:20px;background:#fff2;font-size:11px}
    .panel{pointer-events:auto;position:fixed;right:24px;bottom:86px;width:388px;max-width:calc(100% - 24px);max-height:calc(100dvh - 110px);background:#fff;border:1px solid #dfebe7;border-radius:16px;box-shadow:0 16px 64px #183b3833;display:flex;flex-direction:column;overflow:hidden}
    header{display:flex;gap:11px;align-items:center;padding:18px 18px 13px;border-bottom:1px solid #edf2ef}.brand{width:36px;height:36px;border-radius:10px;background:#eaf5f1;color:#16847a;display:grid;place-items:center}.heading{flex:1;min-width:0}h2{margin:0;font-size:16px;font-weight:650;letter-spacing:.2px}.subtitle{color:#799088;font-size:11px;margin-top:2px}.icon-button{display:grid;place-items:center;width:32px;height:32px;border-radius:7px;background:transparent;color:#70877f}.icon-button:hover{background:#edf5f1;color:#16847a}
    .body{padding:14px 18px;overflow:auto;overscroll-behavior:contain}.actions{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}.soft{display:flex;align-items:center;justify-content:center;gap:6px;min-height:35px;padding:7px 10px;border-radius:8px;background:#f0f6f3;color:#31594c;font-size:12px}.soft svg{width:16px;height:16px}.soft:hover{background:#e3f0ea}.soft[aria-pressed=true]{color:#117368;background:#dff2e9}.mode{flex:1}.mode-dot{width:6px;height:6px;border-radius:50%;background:#94aaa2}.mode[aria-pressed=true] .mode-dot{background:#16847a}
    .current{width:100%;justify-content:flex-start;margin-bottom:10px}.current span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.selection-heading{display:flex;align-items:center;gap:8px;font-size:12px;margin-top:14px;margin-bottom:8px}.selection-heading strong{font-weight:600;flex:1}.muted{color:#84958e;font-size:11px}.text-button{background:transparent;color:#7c8e87;font-size:11px;padding:4px}.text-button:hover{color:#16847a}
    .empty{text-align:center;padding:22px 16px;color:#93a49d;background:#f8faf9;border:1px dashed #e0e8e4;border-radius:10px}.empty svg{width:27px;height:27px;display:block;margin:0 auto 9px;color:#91b3a4}.empty p{margin:0}.empty small{display:block;margin-top:5px;font-size:11px}
    ol{list-style:none;padding:0;margin:0}.selected-row{display:flex;align-items:center;gap:9px;padding:10px 0;border-bottom:1px solid #eff3f0}.selected-row:last-child{border-bottom:0}.row-icon{width:32px;height:34px;display:grid;place-items:center;flex-shrink:0;background:#f0f6f3;border-radius:7px;color:#67917e}.row-info{flex:1;min-width:0}.row-title{font-size:12px;line-height:1.55;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere}.row-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;align-items:center;font-size:10px;color:#8a9b93}.result{color:#16847a}.result.warn{color:#9a6b29}.row-remove{width:26px;height:28px;flex-shrink:0}.row-remove svg{width:15px;height:15px}
    details{margin-top:12px;border-top:1px solid #edf2ef;padding-top:10px}summary{display:flex;align-items:center;gap:7px;color:#627e70;font-size:12px;cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary svg{width:15px;height:15px}summary .arrow{margin-left:auto;transition:transform .15s}details[open] summary .arrow{transform:rotate(180deg)}textarea{resize:vertical;min-height:76px;max-height:160px;width:100%;margin:10px 0 8px}input,textarea{border:1px solid #dce7e1;border-radius:8px;padding:9px 10px;background:#fff;color:#264b3d;line-height:1.5;font-size:12px;min-width:0}.manual-add{margin-left:auto}.connection{margin-bottom:14px;padding:12px;border-radius:10px;border:1px solid #e1ebe6;background:#f7faf8}.connection h3{margin:0 0 10px;font-size:13px;font-weight:600}.connection label{display:block;margin-top:9px;font-size:11px;color:#61796e}.connection input{display:block;width:100%;margin-top:5px}.connection-note{font-size:10px;color:#8a7762;margin:7px 0}.connection .soft{width:100%;margin-top:10px;background:#e4f2e9}.connection small{display:block;margin-top:7px;font-size:10px;color:#84958e}.connected{display:flex;gap:7px;align-items:center;padding:6px 0;font-size:11px;color:#70867b}.connected span{flex:1;overflow-wrap:anywhere}.connected button{flex-shrink:0}.connected svg{width:15px;height:15px}.policy{font-size:10px;color:#87998f;margin-bottom:10px;overflow-wrap:anywhere}
    footer{padding:12px 18px 16px;border-top:1px solid #e7efea;background:#fbfdfb}.notice{font-size:11px;line-height:1.6;color:#6c8375;margin:0 0 10px;overflow-wrap:anywhere}.notice[data-tone=error]{color:#a95143}.notice[data-tone=success]{color:#16785e}.submit{display:flex;justify-content:center;align-items:center;gap:8px;width:100%;min-height:41px;padding:10px 15px;border-radius:9px;color:#fff;background:#16847a;font-weight:600;font-size:13px}.submit:hover:not(:disabled){background:#106e64}.submit svg{width:17px;height:17px}.footnote{margin-top:7px;font-size:10px;text-align:center;color:#95a79e}
    .marker{pointer-events:auto;position:fixed;display:grid;place-items:center;width:31px;height:31px;background:#ffffffed;color:#567267;border:1px solid #d2e7dc;border-radius:8px;box-shadow:0 2px 10px #16352a24;z-index:1}.marker:hover{background:#effbf4}.marker[aria-pressed=true]{color:#fff;background:#16847a;border-color:#16847a}.marker svg{width:19px;height:19px}.panel,.fab{z-index:3}.toast{pointer-events:none;position:fixed;right:24px;bottom:87px;max-width:330px;padding:10px 14px;color:#fff;background:#234b40ed;border-radius:9px;font-size:12px;z-index:4;box-shadow:0 4px 18px #142e3222}
    @media(max-width:520px){.fab{right:14px;bottom:max(18px,env(safe-area-inset-bottom));padding:11px 14px}.panel{right:12px;bottom:78px;width:calc(100% - 24px);max-height:calc(100dvh - 96px);border-radius:14px}header{padding:14px 15px 11px}.body{padding:12px 15px}footer{padding:11px 15px 13px}.marker{width:35px;height:35px}.toast{right:14px;bottom:80px;max-width:calc(100% - 28px)}}
    @media(prefers-reduced-motion:reduce){*{transition:none!important}}
  </style><div class="markers"></div>
  <button class="fab" type="button" aria-label="打开 Treasure Up 选片助手" aria-expanded="false">${icon('library')}<span>选片</span><span class="fab-count">0</span></button>
  <section class="panel" hidden role="dialog" aria-label="Treasure Up 选片助手">
    <header><div class="brand">${icon('library')}</div><div class="heading"><h2>Treasure Up</h2><div class="subtitle">挑选喜欢的视频，留在自己的收藏里</div></div><button class="icon-button settings" title="连接设置" aria-label="连接设置">${icon('settings')}</button><button class="icon-button close" title="收起" aria-label="收起选片助手">${icon('close')}</button></header>
    <div class="body">
      <form class="connection" hidden><h3>连接我的视频库</h3><label>服务地址<input class="backend" type="url" placeholder="https://archive.example.com" autocomplete="off" spellcheck="false" required></label><p class="connection-note" hidden>局域网明文连接 · 仅在可信本机或局域网使用</p><small class="token-state">尚未设置专用令牌</small><button class="soft save" type="submit">${icon('link')}保存并设置令牌</button><small>令牌将在浏览器原生对话框中填写，不输入 B站页面。</small></form>
      <div class="connected">${icon('link')}<span class="connection-label">尚未连接视频库</span><button class="text-button test" type="button">测试连接</button></div><div class="policy" hidden></div>
      <button class="soft current" type="button" hidden>${icon('plus')}<span>加入当前视频</span></button>
      <div class="actions"><button class="soft mode" type="button" aria-pressed="false"><span class="mode-dot"></span><span class="mode-label">开启页面选片</span></button><button class="soft visible" type="button">${icon('select')}选择可见视频</button></div>
      <div class="selection-heading"><strong>已选视频 <span class="selected-count">0</span></strong><span class="muted">最多 50 个</span><button class="text-button clear" type="button">清空</button></div>
      <div class="empty">${icon('video')}<p>把想保存的视频放进来</p><small>开启选片勾选卡片，或粘贴 BV 号</small></div><ol class="selection"></ol>
      <details class="results" hidden open><summary>${icon('check')}上次提交结果<span class="arrow">${icon('chevron')}</span></summary><ol class="results-list"></ol></details>
      <details class="manual"><summary>${icon('paste')}粘贴 BV 号或视频链接<span class="arrow">${icon('chevron')}</span></summary><textarea class="manual-text" aria-label="BV 号或视频链接" placeholder="支持多个 BV 号或 B站视频链接，每行一个" maxlength="20000"></textarea><button class="soft manual-add" type="button">${icon('plus')}加入已选</button></details>
    </div><footer><p class="notice" role="status" aria-live="polite" hidden></p><button class="submit" type="button" disabled>${icon('send')}<span>提交到视频库</span></button><div class="footnote">只提交 BV 号 · 下载由你的服务完成</div></footer>
  </section><div class="toast" role="status" hidden></div>`;
  document.documentElement.append(host);
  const $ = (selector) => shadow.querySelector(selector);
  const panel = $('.panel'),
    fab = $('.fab'),
    markers = $('.markers');
  let config = { backend: '', token: '' },
    selected = new Map(),
    selecting = false,
    busy = false,
    activity = '';
  let ready = false,
    pageUrl = location.href,
    observerActive = false,
    scanTimer = 0,
    frame = 0,
    toastTimer = 0;
  let connectionInfo = null;
  let lastResults = [];
  const cards = new Map(),
    pendingRoots = new Set();
  const statuses = {
    queued: '已加入队列',
    active: '正在处理',
    existing: '已有归档',
    needs_attention: '需在后台处理',
    deleted: '已删除，未重新采集',
    daily_limit: '今日新增任务已达上限',
    failed: '未提交，请重试',
  };
  const draft = createDraftStore(GM, (items) => {
    selected = items;
    render();
    schedulePositions();
  });
  async function removeSelection(bvids) {
    try {
      await draft.change({ type: 'remove', bvids });
    } catch {
      say('选择未能保存，请稍后重试。', 'error');
    }
  }
  function say(text, tone = '') {
    $('.notice').textContent = safeError(text, config.token);
    $('.notice').dataset.tone = tone;
    $('.notice').hidden = !text;
    if (panel.hidden && text) {
      $('.toast').textContent = safeError(text, config.token);
      $('.toast').hidden = false;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        $('.toast').hidden = true;
      }, 4000);
    }
  }
  function render() {
    $('.fab-count').textContent = String(selected.size);
    $('.selected-count').textContent = String(selected.size);
    $('.empty').hidden = selected.size > 0;
    $('.mode').setAttribute('aria-pressed', String(selecting));
    $('.mode-label').textContent = selecting ? '页面选片已开启' : '开启页面选片';
    const list = $('.selection');
    list.replaceChildren();
    for (const item of selected.values()) {
      const row = document.createElement('li');
      row.className = 'selected-row';
      row.innerHTML = `<span class="row-icon">${icon('video')}</span><div class="row-info"><div class="row-title"></div><div class="row-meta"><span class="row-bvid"></span><span class="result"></span></div></div><button class="icon-button row-remove" type="button">${icon('close')}</button>`;
      row.querySelector('.row-title').textContent = item.title;
      row.querySelector('.row-bvid').textContent = item.bvid;
      row.querySelector('.result').textContent = item.status
        ? statuses[item.status] || '未知结果，请在后台确认'
        : '';
      row
        .querySelector('.result')
        .classList.toggle(
          'warn',
          ['needs_attention', 'deleted', 'daily_limit', 'failed', 'unknown'].includes(item.status),
        );
      const remove = row.querySelector('button');
      remove.setAttribute('aria-label', `移除 ${item.title}`);
      remove.disabled = busy;
      remove.addEventListener('click', () => {
        if (!busy) void removeSelection([item.bvid]);
      });
      list.append(row);
    }
    const pending = pendingItems(selected).length;
    $('.submit').disabled = busy || !ready || pending === 0;
    $('.submit span').textContent =
      activity === 'submit'
        ? '正在提交…'
        : pending
          ? `提交 ${pending} 个视频`
          : selected.size
            ? '已处理所选视频'
            : '提交到视频库';
    for (const selector of [
      '.clear',
      '.manual-add',
      '.visible',
      '.current',
      '.save',
      '.test',
      '.backend',
    ])
      $(selector).disabled = busy || !ready;
    $('.clear').disabled = busy || !selected.size;
    $('.test').textContent = activity === 'probe' ? '连接中…' : '测试连接';
    $('.connection-label').textContent = connectionInfo
      ? `已连接 · ${connectionInfo.account?.name || '采集账号'}`
      : config.backend
        ? `已保存 · ${config.backend}`
        : '尚未连接视频库';
    $('.token-state').textContent = config.token
      ? '专用令牌已保存在油猴私有存储'
      : '尚未设置专用令牌';
    $('.results').hidden = !lastResults.length;
    $('.results-list').replaceChildren();
    for (const item of lastResults) {
      const row = document.createElement('li');
      row.className = 'selected-row';
      row.innerHTML = `<div class="row-info"><div class="row-title"></div><div class="row-meta"><span class="row-bvid"></span><span class="result"></span></div></div>`;
      row.querySelector('.row-title').textContent = item.title;
      row.querySelector('.row-bvid').textContent = item.bvid;
      row.querySelector('.result').textContent = statuses[item.status] || '未知结果，请在后台确认';
      row
        .querySelector('.result')
        .classList.toggle('warn', !['queued', 'active', 'existing'].includes(item.status));
      $('.results-list').append(row);
    }
    $('.policy').hidden = !connectionInfo;
    if (connectionInfo) {
      const policy = connectionInfo.policy || {};
      const parts = [
        policy.quality === 'best'
          ? '最高可用画质'
          : policy.quality
            ? `画质 ${String(policy.quality).toUpperCase()}`
            : '',
        policy.download_media ? '视频' : '',
        policy.fetch_danmaku ? '弹幕' : '',
        policy.fetch_comments ? '评论' : '',
        policy.fetch_subtitles ? '字幕' : '',
      ].filter(Boolean);
      $('.policy').textContent = parts.join(' · ');
    }
  }
  function currentVideo() {
    const bvid = videoFromUrl(location.href);
    return bvid
      ? {
          bvid,
          title: (document.querySelector('h1')?.textContent || document.title || bvid)
            .trim()
            .replace(/_哔哩哔哩_bilibili$/, '')
            .slice(0, 200),
        }
      : null;
  }
  function updateCurrent() {
    const item = currentVideo();
    $('.current').hidden = !item;
    $('.current span').textContent = item ? `加入当前视频 · ${item.title}` : '加入当前视频';
    $('.current').title = item?.title || '';
  }
  function openSettings() {
    $('.connection').hidden = !$('.connection').hidden;
    if (!$('.connection').hidden) {
      $('.backend').value = config.backend;
      localHint();
      $('.backend').focus({ preventScroll: true });
    }
  }
  function setPanel(open) {
    panel.hidden = !open;
    fab.setAttribute('aria-expanded', String(open));
    $('.toast').hidden = true;
    if (open) {
      updateCurrent();
      if (!config.backend && $('.connection').hidden) openSettings();
    } else fab.focus({ preventScroll: true });
    updateObserver();
  }
  function localHint() {
    $('.connection-note').hidden = !/^http:\/\//i.test($('.backend').value.trim());
  }
  async function addCandidates(items) {
    if (busy) return;
    try {
      const merged = await draft.change({ type: 'add', items });
      say(
        merged.overflow
          ? `已选满 50 个视频，另有 ${merged.overflow} 个未加入。`
          : merged.added
            ? `已加入 ${merged.added} 个视频。`
            : '这些视频已经在已选列表中。',
      );
    } catch {
      say('选择未能保存，请稍后重试。', 'error');
    }
  }
  function cardInfo(node) {
    const bvid = BVID.test(node.getAttribute('data-bvid') || '')
      ? node.getAttribute('data-bvid')
      : videoFromUrl(node.getAttribute('href'), location.href);
    if (!bvid) return;
    const card = node.closest(CARD) || node;
    const title = (
      card.querySelector(
        'h3,h2,[class*="info--tit"],[class*="video-title"],[class*="video_title"],.title',
      )?.textContent ||
      node.getAttribute('title') ||
      card.querySelector('img')?.alt ||
      node.textContent ||
      bvid
    )
      .trim()
      .replace(/\s+/g, ' ')
      .slice(0, 200);
    cards.set(card, { bvid, title: title || bvid });
  }
  function scan(root) {
    if (root === host || (root instanceof Element && host.contains(root))) return;
    if (root instanceof Element && root.matches(LINKS)) cardInfo(root);
    for (const node of root.querySelectorAll?.(LINKS) || []) cardInfo(node);
  }
  function scheduleScan(root = document) {
    if (!observerActive) return;
    if (pendingRoots.size > 100) {
      pendingRoots.clear();
      pendingRoots.add(document);
    } else pendingRoots.add(root);
    if (scanTimer) return;
    scanTimer = setTimeout(() => {
      scanTimer = 0;
      if (!observerActive) return;
      const roots = pendingRoots.has(document) ? [document] : [...pendingRoots];
      pendingRoots.clear();
      if (roots[0] === document) cards.clear();
      for (const root of roots) if (root === document || root.isConnected) scan(root);
      for (const [card] of cards) if (!card.isConnected) cards.delete(card);
      updateCurrent();
      schedulePositions();
    }, 220);
  }
  function visibleCards() {
    const result = [],
      seen = new Set();
    for (const [card, item] of cards) {
      if (!card.isConnected) {
        cards.delete(card);
        continue;
      }
      const rect = card.getBoundingClientRect();
      if (
        rect.width < 40 ||
        rect.height < 24 ||
        rect.bottom <= 0 ||
        rect.top >= innerHeight ||
        rect.right <= 0 ||
        rect.left >= innerWidth ||
        !card.getClientRects().length ||
        seen.has(item.bvid)
      )
        continue;
      if (getComputedStyle(card).visibility === 'hidden') continue;
      seen.add(item.bvid);
      result.push({ ...item, rect });
    }
    return result;
  }
  function positionMarkers() {
    frame = 0;
    markers.replaceChildren();
    if (!selecting || document.hidden || document.fullscreenElement) return;
    for (const item of visibleCards()) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'marker';
      button.style.left = `${Math.max(5, Math.min(innerWidth - 40, item.rect.left + 7))}px`;
      button.style.top = `${Math.max(5, item.rect.top + 7)}px`;
      button.setAttribute(
        'aria-label',
        `${selected.has(item.bvid) ? '取消选择' : '选择'} ${item.title}`,
      );
      button.setAttribute('aria-pressed', String(selected.has(item.bvid)));
      button.disabled = busy;
      button.innerHTML = icon(selected.has(item.bvid) ? 'check' : 'plus');
      button.addEventListener('click', () => {
        if (busy) return;
        if (selected.has(item.bvid)) void removeSelection([item.bvid]);
        else void addCandidates([item]);
      });
      markers.append(button);
    }
  }
  function schedulePositions() {
    if (!frame && selecting) frame = requestAnimationFrame(positionMarkers);
  }
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      if (record.target === host || host.contains(record.target)) continue;
      if (record.type === 'attributes') scheduleScan(record.target);
      else for (const node of record.addedNodes) if (node.nodeType === 1) scheduleScan(node);
    }
  });
  function updateObserver() {
    const active = selecting || !panel.hidden;
    if (active === observerActive) return;
    observerActive = active;
    if (active) {
      observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['href', 'data-bvid'],
      });
      scheduleScan();
    } else {
      observer.disconnect();
      clearTimeout(scanTimer);
      scanTimer = 0;
      pendingRoots.clear();
      cards.clear();
    }
  }
  function routeChanged() {
    if (location.href === pageUrl) return;
    pageUrl = location.href;
    cards.clear();
    updateCurrent();
    markers.replaceChildren();
    scheduleScan();
  }
  async function testConnection() {
    if (busy || !ready) return;
    if (!config.backend || !config.token) {
      if ($('.connection').hidden) openSettings();
      say('先填写服务地址与专用令牌。');
      return;
    }
    busy = true;
    activity = 'probe';
    connectionInfo = null;
    render();
    try {
      connectionInfo = await createClient(GM, config).status();
      say('连接成功，可以提交已选视频。', 'success');
    } catch (error) {
      say(safeError(error.message, config.token), 'error');
    } finally {
      busy = false;
      activity = '';
      render();
      schedulePositions();
    }
  }
  fab.addEventListener('click', () => setPanel(panel.hidden));
  $('.close').addEventListener('click', () => setPanel(false));
  $('.settings').addEventListener('click', openSettings);
  $('.backend').addEventListener('input', localHint);
  $('.test').addEventListener('click', (event) => {
    if (event.isTrusted) void testConnection();
  });
  $('.mode').addEventListener('click', () => {
    selecting = !selecting;
    render();
    if (!selecting) markers.replaceChildren();
    updateObserver();
    schedulePositions();
  });
  $('.visible').addEventListener('click', () => {
    if (busy) return;
    scan(document);
    addCandidates(visibleCards());
  });
  $('.current').addEventListener('click', () => {
    const item = currentVideo();
    if (item) addCandidates([item]);
  });
  $('.clear').addEventListener('click', () => {
    if (busy) return;
    void removeSelection([...selected.keys()]);
    say('');
  });
  $('.manual-add').addEventListener('click', () => {
    const ids = extractBvids($('.manual-text').value);
    if (!ids.length) {
      say('未识别到 BV 号。短链接请先在 B站打开，再复制完整视频链接。', 'error');
      return;
    }
    addCandidates(ids.map((bvid) => ({ bvid, title: bvid })));
    $('.manual-text').value = '';
  });
  $('.connection').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!event.isTrusted || busy || !ready) return;
    if (!supportedManager(GM.info)) {
      say('需要 Tampermonkey 5.0+ 的 DOM 隔离环境；未读取或输入令牌。', 'error');
      return;
    }
    let inputToken = '';
    try {
      const backend = normalizeBackend($('.backend').value);
      // Native browser prompt is outside the page DOM. A closed shadow input still leaks paste/keydown events to page capture listeners.
      inputToken = window.prompt(
        `为 ${backend} 设置 Treasure Up 专用令牌\n\n粘贴后台「浏览器采集」创建的 tu_ingest_ 令牌。${backend === config.backend && config.token ? '\n留空可保留已保存令牌。' : ''}`,
        '',
      );
      if (inputToken === null) return;
      const next = nextConfig(config, backend, inputToken);
      busy = true;
      activity = 'probe';
      connectionInfo = null;
      render();
      await GM.setValue(STORAGE, next);
      config = next;
      $('.backend').value = config.backend;
      render();
      connectionInfo = await createClient(GM, next).status();
      say('连接成功，可以提交已选视频。', 'success');
    } catch (error) {
      say(safeError(error.message, inputToken || config.token), 'error');
    } finally {
      busy = false;
      activity = '';
      render();
      schedulePositions();
    }
  });
  $('.submit').addEventListener('click', async (event) => {
    if (!event.isTrusted || busy || !ready) return;
    if (!config.backend || !config.token) {
      if ($('.connection').hidden) openSettings();
      say('请先连接自己的视频库。');
      return;
    }
    const bvids = pendingItems(selected).map((item) => item.bvid);
    if (!bvids.length) return;
    busy = true;
    activity = 'submit';
    render();
    schedulePositions();
    try {
      const result = await createClient(GM, config).submit(bvids);
      if (!Array.isArray(result.items))
        throw new Error('提交响应格式异常；请在后台确认任务状态后重试。');
      const returned = new Map(
        result.items
          .filter((item) => bvids.includes(item?.bvid))
          .map((item) => [item.bvid, item.status]),
      );
      lastResults = bvids.map((bvid) => ({
        bvid,
        title: selected.get(bvid)?.title || bvid,
        status: returned.get(bvid) || 'failed',
      }));
      await draft.change({
        type: 'remove',
        bvids: lastResults
          .filter((item) => ['queued', 'active', 'existing'].includes(item.status))
          .map((item) => item.bvid),
      });
      const queued = result.items.filter((item) =>
        ['queued', 'active'].includes(item.status),
      ).length;
      const limited = result.items.filter((item) => item.status === 'daily_limit').length;
      say(
        limited
          ? `已处理本批请求，${limited} 个视频达到今日新增上限，已保留待提交。`
          : queued
            ? `${queued} 个视频已在任务队列中，其余结果见列表。`
            : '提交完成，逐条结果见列表。',
        'success',
      );
    } catch (error) {
      say(safeError(error.message, config.token), 'error');
    } finally {
      busy = false;
      activity = '';
      render();
      schedulePositions();
    }
  });
  // Events from our explicit buttons never trigger B站's page-level card handlers.
  shadow.addEventListener('click', (event) => event.stopPropagation());
  shadow.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !panel.hidden) {
      event.preventDefault();
      event.stopPropagation();
      setPanel(false);
    }
  });
  window.addEventListener('urlchange', routeChanged);
  window.addEventListener('popstate', routeChanged);
  window.addEventListener('hashchange', routeChanged);
  window.addEventListener('scroll', schedulePositions, { passive: true, capture: true });
  window.addEventListener('resize', schedulePositions, { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) markers.replaceChildren();
    else {
      routeChanged();
      schedulePositions();
    }
  });
  document.addEventListener('fullscreenchange', schedulePositions);
  if (typeof GM.registerMenuCommand === 'function')
    GM.registerMenuCommand('打开 Treasure Up 选片助手', () => setPanel(true));
  void (async () => {
    try {
      if (supportedManager(GM.info)) {
        const saved = await GM.getValue(STORAGE, null);
        if (saved && TOKEN.test(saved.token || ''))
          config = { backend: normalizeBackend(saved.backend), token: saved.token };
      }
      await draft.refresh();
    } catch {
      say('连接设置未能读取，请重新配置。', 'error');
    }
    ready = true;
    updateCurrent();
    render();
    if (!supportedManager(GM.info))
      say('需要 Tampermonkey 5.0+ 的 DOM 隔离环境；当前仅可选片，未读取令牌。', 'error');
  })();
})();
