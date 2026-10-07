// ==UserScript==
// @name         Treasure Up · B站选片助手
// @namespace    treasure-up
// @version      1.3.0
// @description  在B站选中视频，批量加入自己的 Treasure Up 备份库。需要 Tampermonkey 5.0+。
// @author       Treasure Up
// @icon         data:image/png;base64,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
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
// @grant        GM_getTab
// @grant        GM_saveTab
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
  const TOKEN = /^tu_ingest_[A-Za-z0-9_-]{43}$/;
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
  function responseError(status, detail, token = '') {
    const help = {
      401: '令牌无效、已过期或已撤销。请在后台「浏览器采集」新建令牌，再到连接设置中替换。',
      403: '访问被拒绝。请检查令牌所属管理员是否有效，以及反向代理的访问限制。',
      404: '没有找到选片助手接口。请使用视频库站点根地址，并确认服务已升级到支持浏览器采集的版本。',
      409: '采集账号不可用。请在后台「B站账号」中更新或验证令牌绑定的账号。',
      422: '提交参数未通过校验。请检查 BV 号，并从后台重新安装最新版选片助手。',
      429: '提交过于频繁。已选视频仍保留，请稍后手动重试，不必连续点击。',
    };
    const message =
      help[status] ||
      (status >= 500
        ? '服务暂时不可用。请确认视频库可以打开，稍后再试；提交超时后可先到后台查看任务。'
        : '请求失败，请检查服务配置。');
    return `HTTP ${status} · ${message}${detail ? `\n服务提示：${safeError(detail, token)}` : ''}`;
  }
  function environmentIssue(gm) {
    if (!supportedManager(gm?.info))
      return '需要 Tampermonkey 5.0+ 的 DOM 隔离环境。请更新扩展与脚本；在 Tampermonkey 设置的「安全 → 沙盒模式」允许 DOM 后刷新 B站。不要改为页面沙箱。';
    if (['getValue', 'setValue', 'xmlHttpRequest'].some((name) => typeof gm[name] !== 'function'))
      return '脚本权限不完整。请从后台重新安装选片助手，确认安装后刷新 B站页面。';
    return '';
  }
  function tabStorage(gm, legacyGet, legacySave, onFallback, timeoutMs = 2500) {
    let memory = {},
      volatile = false;
    const timed = (operation) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Tab storage timeout')), timeoutMs);
        Promise.resolve()
          .then(operation)
          .then(resolve, reject)
          .finally(() => clearTimeout(timer));
      });
    function fallback() {
      if (!volatile) onFallback();
      volatile = true;
    }
    return {
      async getTab() {
        if (!volatile) {
          try {
            // Earlier Tampermonkey builds exposed an async getTab that returned
            // undefined. The documented callback API works on those builds.
            const value = await timed(() =>
              typeof legacyGet === 'function'
                ? new Promise((resolve) => legacyGet(resolve))
                : gm.getTab(),
            );
            if (!value || typeof value !== 'object' || Array.isArray(value))
              throw new Error('Invalid tab storage');
            memory = value;
          } catch {
            fallback();
          }
        }
        return { ...memory };
      },
      async saveTab(value) {
        memory = { ...value };
        if (!volatile) {
          try {
            await timed(() =>
              typeof legacySave === 'function'
                ? new Promise((resolve) => legacySave(value, resolve))
                : gm.saveTab(value),
            );
          } catch {
            fallback();
          }
        }
      },
    };
  }
  function reconcileMarkers(nodes, items, create, update) {
    const visible = new Set(items.map((item) => item.bvid));
    for (const [bvid, node] of nodes) {
      if (!visible.has(bvid)) {
        node.remove();
        nodes.delete(bvid);
      }
    }
    for (const item of items) {
      let node = nodes.get(item.bvid);
      if (!node) {
        node = create(item);
        nodes.set(item.bvid, node);
      }
      update(node, item);
    }
  }
  // A virtualized card can keep its DOM node while its old link disappears.
  function invalidateCards(cards, root) {
    for (const [card] of cards)
      if (!card.isConnected || card === root || root.contains?.(card)) cards.delete(card);
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
        if (!response.status)
          throw new Error(
            '没有收到服务响应。请先直接打开服务地址，确认网络、证书与 Tampermonkey 的跨域访问授权；另一台设备不能使用服务器的 localhost。',
          );
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
            if (response.status < 200 || response.status >= 300)
              throw new Error(responseError(response.status));
            throw new Error('服务没有返回有效 JSON，请确认这是 Treasure Up 的站点地址。');
          }
        }
        if (response.status < 200 || response.status >= 300)
          throw new Error(responseError(response.status, body.detail, config.token));
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
  async function verifyAndSaveConnection(gm, next, storageKey) {
    const verified = await createClient(gm, next).status();
    await gm.setValue(storageKey, next);
    return verified;
  }
  // CORE-END

  if (typeof GM === 'undefined' || !document.body) return;
  const BRAND_MARK =
    'data:image/png;base64,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';
  const BRAND_MARK =
    'data:image/png;base64,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';
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
  const brandImage = `<img class="brand-image" src="${BRAND_MARK}" alt="" aria-hidden="true" width="36" height="36">`;
  const host = document.createElement('div');
  host.setAttribute('data-treasure-up', 'collector');
  host.style.cssText = 'all:initial;position:fixed;inset:0;z-index:2147483000;pointer-events:none;';
  const shadow = host.attachShadow({ mode: 'closed' });
  // All HTML is constant. Page titles, API messages and identifiers are inserted with textContent below.
  shadow.innerHTML = `<style>
    :host{font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;color:#303e7f;font-size:13px;line-height:1.5}
    *,*:before,*:after{box-sizing:border-box}button,input,textarea{font:inherit}button{cursor:pointer}button:disabled{opacity:.45;cursor:default}svg{width:19px;height:19px;flex-shrink:0}button{border:0;color:inherit}button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:2px solid #303e7f;outline-offset:3px}[hidden]{display:none!important}
    .fab{pointer-events:auto;position:fixed;right:24px;bottom:28px;display:flex;gap:8px;align-items:center;padding:12px 16px;border-radius:999px;background:#303e7f;color:#fff;box-shadow:0 5px 20px #303e7f30;font-weight:600;letter-spacing:.3px}.fab:hover{background:#263266}.fab-count{min-width:20px;padding:0 5px;border-radius:20px;background:#fff2;font-size:11px}
    .fab-label{display:grid;text-align:left}.fab-label small{font-size:10px;font-weight:400;opacity:.85}.quick{pointer-events:auto;position:fixed;right:24px;bottom:92px;display:flex;gap:6px;align-items:center;padding:9px 12px;border:1px solid #dbe5f7;border-radius:9px;background:#fff;color:#303e7f;box-shadow:0 3px 14px #303e7f18;z-index:2}.quick svg{width:16px;height:16px}.help{font-size:11px;color:#61796e;line-height:1.8}.help p{margin:8px 0}.library{color:#303e7f;text-decoration:none;font-size:11px}.library:hover{text-decoration:underline}
    .panel{pointer-events:auto;position:fixed;right:24px;bottom:102px;width:388px;max-width:calc(100% - 24px);max-height:calc(100dvh - 126px);background:#fff;border:1px solid #dfebe7;border-radius:16px;box-shadow:0 16px 64px #183b3833;display:flex;flex-direction:column;overflow:hidden}
    header{display:flex;gap:11px;align-items:center;padding:18px 18px 13px;border-bottom:1px solid #edf3ff}.brand{width:36px;height:36px;border-radius:10px;background:#edf3ff;color:#303e7f;display:grid;place-items:center}.heading{flex:1;min-width:0}h2{margin:0;font-size:16px;font-weight:650;letter-spacing:.2px}.subtitle{color:#75829b;font-size:11px;margin-top:2px}.icon-button{display:grid;place-items:center;width:32px;height:32px;border-radius:7px;background:transparent;color:#75829b}.icon-button:hover{background:#edf3ff;color:#303e7f}
    .body{padding:14px 18px;overflow:auto;overscroll-behavior:contain}.actions{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}.soft{display:flex;align-items:center;justify-content:center;gap:6px;min-height:35px;padding:7px 10px;border-radius:8px;background:#f0f6f3;color:#31594c;font-size:12px}.soft svg{width:16px;height:16px}.soft:hover{background:#e3f0ea}.soft[aria-pressed=true]{color:#117368;background:#dff2e9}.mode{flex:1}.mode-dot{width:6px;height:6px;border-radius:50%;background:#94aaa2}.mode[aria-pressed=true] .mode-dot{background:#303e7f}
    .current{width:100%;justify-content:flex-start;margin-bottom:10px}.current span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.selection-heading{display:flex;align-items:center;gap:8px;font-size:12px;margin-top:14px;margin-bottom:8px}.selection-heading strong{font-weight:600;flex:1}.muted{color:#84958e;font-size:11px}.text-button{background:transparent;color:#7c8e87;font-size:11px;padding:4px}.text-button:hover{color:#303e7f}
    .empty{text-align:center;padding:22px 16px;color:#93a49d;background:#f8faf9;border:1px dashed #e0e8e4;border-radius:10px}.empty svg{width:27px;height:27px;display:block;margin:0 auto 9px;color:#91b3a4}.empty p{margin:0}.empty small{display:block;margin-top:5px;font-size:11px}
    ol{list-style:none;padding:0;margin:0}.selected-row{display:flex;align-items:center;gap:9px;padding:10px 0;border-bottom:1px solid #eff3f0}.selected-row:last-child{border-bottom:0}.row-icon{width:32px;height:34px;display:grid;place-items:center;flex-shrink:0;background:#f0f6f3;border-radius:7px;color:#67917e}.row-info{flex:1;min-width:0}.row-title{font-size:12px;line-height:1.55;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere}.row-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;align-items:center;font-size:10px;color:#8a9b93}.result{color:#303e7f}.result.warn{color:#9a6b29}.row-remove{width:26px;height:28px;flex-shrink:0}.row-remove svg{width:15px;height:15px}
    details{margin-top:12px;border-top:1px solid #edf3ff;padding-top:10px}summary{display:flex;align-items:center;gap:7px;color:#627e70;font-size:12px;cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary svg{width:15px;height:15px}summary .arrow{margin-left:auto;transition:transform .15s}details[open] summary .arrow{transform:rotate(180deg)}textarea{resize:vertical;min-height:76px;max-height:160px;width:100%;margin:10px 0 8px}input,textarea{border:1px solid #dce7e1;border-radius:8px;padding:9px 10px;background:#fff;color:#264b3d;line-height:1.5;font-size:12px;min-width:0}.manual-add{margin-left:auto}.connection{margin-bottom:14px;padding:12px;border-radius:10px;border:1px solid #e1ebe6;background:#f7faf8}.connection h3{margin:0 0 10px;font-size:13px;font-weight:600}.connection label{display:block;margin-top:9px;font-size:11px;color:#61796e}.connection input{display:block;width:100%;margin-top:5px}.connection-note{font-size:10px;color:#8a7762;margin:7px 0}.connection .soft{width:100%;margin-top:10px;background:#e6eeff}.connection small{display:block;margin-top:7px;font-size:10px;color:#84958e}.connected{display:flex;gap:7px;align-items:center;padding:6px 0;font-size:11px;color:#70867b}.connected span{flex:1;overflow-wrap:anywhere}.connected button{flex-shrink:0}.connected svg{width:15px;height:15px}.policy{font-size:10px;color:#87998f;margin-bottom:10px;overflow-wrap:anywhere}
    footer{padding:12px 18px 16px;border-top:1px solid #dbe5f7;background:#f8faff}.notice{font-size:11px;line-height:1.6;color:#6c8375;margin:0 0 10px;overflow-wrap:anywhere}.notice[data-tone=error]{color:#a95143}.notice[data-tone=success]{color:#16785e}.submit{display:flex;justify-content:center;align-items:center;gap:8px;width:100%;min-height:41px;padding:10px 15px;border-radius:9px;color:#fff;background:#303e7f;font-weight:600;font-size:13px}.submit:hover:not(:disabled){background:#263266}.submit svg{width:17px;height:17px}.footnote{margin-top:7px;font-size:10px;text-align:center;color:#95a79e}
    .marker{pointer-events:auto;position:fixed;display:grid;place-items:center;width:31px;height:31px;background:#ffffffed;color:#657391;border:1px solid #dbe5f7;border-radius:8px;box-shadow:0 2px 10px #303e7f24;z-index:1}.marker:hover{background:#edf3ff}.marker[aria-pressed=true]{color:#fff;background:#303e7f;border-color:#303e7f}.marker svg{width:19px;height:19px}.panel,.fab{z-index:3}.toast{pointer-events:none;position:fixed;right:24px;bottom:87px;max-width:330px;padding:10px 14px;color:#fff;background:#303e7fed;border-radius:9px;font-size:12px;z-index:4;box-shadow:0 4px 18px #142e3222}
    @media(max-width:520px){.fab{right:14px;bottom:max(18px,env(safe-area-inset-bottom));padding:11px 14px}.quick{right:14px;bottom:88px}.panel{right:12px;bottom:86px;width:calc(100% - 24px);max-height:calc(100dvh - 104px);border-radius:14px}header{padding:14px 15px 11px}.body{padding:12px 15px}footer{padding:11px 15px 13px}.marker{width:35px;height:35px}.toast{right:14px;bottom:88px;max-width:calc(100% - 28px)}}
    @media(prefers-reduced-motion:reduce){*{transition:none!important}}

    .brand-image{display:block;width:36px;height:36px;object-fit:contain;flex-shrink:0}.fab .brand-image{width:34px;height:34px;background:#fff;border-radius:10px;padding:2px}.brand{background:#fff}
  </style><div class="markers"></div>
  <button class="quick" type="button" hidden>${icon('plus')}加入当前视频</button>
  <button class="fab" type="button" aria-label="打开 Treasure Up 选片助手" aria-expanded="false">${brandImage}<span class="fab-label">Treasure Up · 选片<small class="fab-state">正在初始化…</small></span><span class="fab-count">0</span></button>
  <section class="panel" hidden role="dialog" aria-label="Treasure Up 选片助手">
    <header><div class="brand">${brandImage}</div><div class="heading"><h2>Treasure Up</h2><div class="subtitle">挑选喜欢的视频，留在自己的收藏里</div></div><button class="icon-button settings" title="连接设置" aria-label="连接设置">${icon('settings')}</button><button class="icon-button close" title="收起" aria-label="收起选片助手">${icon('close')}</button></header>
    <div class="body">
      <form class="connection" hidden><h3>连接我的视频库</h3><small>先到视频库后台 → 浏览器采集，复制站点地址并创建专用令牌。</small><label>服务地址<input class="backend" type="url" placeholder="https://archive.example.com" autocomplete="off" spellcheck="false" required></label><p class="connection-note" hidden>局域网明文连接 · 仅在可信本机或局域网使用；另一台设备请填写服务器的局域网 IP，不能填写 localhost。</p><small class="token-state">尚未设置专用令牌</small><button class="soft save" type="submit">${icon('link')}验证并保存连接</button><small>点击后在浏览器原生对话框中填写令牌，不输入 B站页面。验证失败会保留原有连接。</small></form>
      <div class="connected">${icon('link')}<span class="connection-label">尚未连接视频库</span><button class="text-button test" type="button">测试连接</button></div><div class="policy" hidden></div>
      <button class="soft current" type="button" hidden>${icon('plus')}<span>加入当前视频</span></button>
      <div class="actions"><button class="soft mode" type="button" aria-pressed="false"><span class="mode-dot"></span><span class="mode-label">开启页面选片</span></button><button class="soft visible" type="button">${icon('select')}选择可见视频</button></div>
      <div class="selection-heading"><strong>已选视频 <span class="selected-count">0</span></strong><span class="muted">最多 50 个</span><button class="text-button clear" type="button">清空</button></div>
      <div class="empty">${icon('video')}<p>把想保存的视频放进来</p><small>开启选片勾选卡片，或粘贴 BV 号</small></div><ol class="selection"></ol><p class="draft-note help" role="status" hidden>浏览器标签存储暂不可用。仍可选片并提交，刷新或离开此页会清空未提交的选择。</p>
      <details class="results" hidden open><summary>${icon('check')}上次提交结果<span class="arrow">${icon('chevron')}</span></summary><ol class="results-list"></ol></details>
      <details class="manual"><summary>${icon('paste')}粘贴 BV 号或视频链接<span class="arrow">${icon('chevron')}</span></summary><textarea class="manual-text" aria-label="BV 号或视频链接" placeholder="支持多个 BV 号或 B站视频链接，每行一个" maxlength="20000"></textarea><button class="soft manual-add" type="button">${icon('plus')}加入已选</button></details>
      <details class="help"><summary>${icon('info')}使用帮助与排查<span class="arrow">${icon('chevron')}</span></summary><p>视频页：点「加入当前视频」。列表页：开启页面选片后，点卡片左上角的 +；也可一次选择屏幕内可见视频。已选列表只在当前标签页内保留。</p><p>没有识别到视频？直播、番剧和短链接无法直接选中，请打开普通 BV 视频页，或展开上面的粘贴入口。</p><p>连接失败时，先直接打开服务地址确认可访问，再检查扩展是否允许连接此地址。公网必须使用 HTTPS；请勿绕过证书警告。</p><a class="library" hidden target="_blank" rel="noopener noreferrer">打开视频库后台 ↗</a></details>
    </div><footer><p class="notice" role="status" aria-live="polite" hidden></p><button class="submit" type="button" disabled>${icon('send')}<span>提交到视频库</span></button><div class="footnote">选片助手 1.3.0 · 下载由你的服务完成</div></footer>
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
    pendingRoots = new Set(),
    markerNodes = new Map();
  function clearMarkers() {
    markers.replaceChildren();
    markerNodes.clear();
  }
  const statuses = {
    queued: '已加入队列',
    active: '正在处理',
    existing: '已有归档',
    needs_attention: '需在后台处理',
    deleted: '已删除，未重新采集',
    daily_limit: '今日新增任务已达上限',
    failed: '未提交，请重试',
  };
  let draftVolatile = false;
  const tab = tabStorage(
    GM,
    typeof GM_getTab === 'function' ? GM_getTab : null,
    typeof GM_saveTab === 'function' ? GM_saveTab : null,
    () => {
      draftVolatile = true;
    },
  );
  const draft = createDraftStore(tab, (items) => {
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
    $('.fab-state').textContent = !ready
      ? '初始化未完成 · 点击查看'
      : connectionInfo
        ? '已连接视频库'
        : config.token
          ? '连接已保存'
          : '首次使用 · 点击连接';
    $('.draft-note').hidden = !draftVolatile;
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
      '.quick',
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
    $('.library').hidden = !config.backend;
    if (config.backend) $('.library').href = `${config.backend}/admin/browser`;
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
    $('.quick').hidden = !item || !panel.hidden;
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
    updateCurrent();
    if (open) {
      if (!config.backend && $('.connection').hidden) openSettings();
    } else fab.focus({ preventScroll: true });
    updateObserver();
  }
  function localHint() {
    $('.connection-note').hidden = !/^http:\/\//i.test($('.backend').value.trim());
  }
  async function addCandidates(items) {
    if (busy || !ready) return;
    if (!items.length) {
      say('当前屏幕内未找到可选 BV 视频。请滚动到视频卡片处，或粘贴完整 BV 链接。');
      return;
    }
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
    invalidateCards(cards, root);
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
    const items =
      selecting && !document.hidden && !document.fullscreenElement ? visibleCards() : [];
    reconcileMarkers(
      markerNodes,
      items,
      (item) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'marker';
        button.addEventListener('click', () => {
          if (busy || !ready) return;
          // A reused/renamed card must submit its current, visible identity.
          const current = visibleCards().find((candidate) => candidate.bvid === item.bvid);
          if (!current) return;
          if (selected.has(item.bvid)) void removeSelection([item.bvid]);
          else void addCandidates([current]);
        });
        markers.append(button);
        return button;
      },
      (button, item) => {
        button.style.left = `${Math.max(5, Math.min(innerWidth - 40, item.rect.left + 7))}px`;
        button.style.top = `${Math.max(5, item.rect.top + 7)}px`;
        button.setAttribute(
          'aria-label',
          `${selected.has(item.bvid) ? '取消选择' : '选择'} ${item.title}`,
        );
        button.setAttribute('aria-pressed', String(selected.has(item.bvid)));
        button.disabled = busy;
        const state = selected.has(item.bvid) ? 'check' : 'plus';
        if (button.dataset.state !== state) {
          button.innerHTML = icon(state);
          button.dataset.state = state;
        }
      },
    );
  }
  function schedulePositions() {
    if (!frame && selecting) frame = requestAnimationFrame(positionMarkers);
  }
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      if (record.target === host || host.contains(record.target)) continue;
      if (record.type === 'attributes') scheduleScan(record.target.closest(CARD) || record.target);
      else {
        const card = record.target.closest?.(CARD);
        if (card) scheduleScan(card);
        else for (const node of record.addedNodes) if (node.nodeType === 1) scheduleScan(node);
        if (record.removedNodes.length) schedulePositions();
      }
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
    clearMarkers();
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
  $('.quick').addEventListener('click', () => {
    const item = currentVideo();
    setPanel(true);
    if (item) void addCandidates([item]);
  });
  $('.close').addEventListener('click', () => setPanel(false));
  $('.settings').addEventListener('click', openSettings);
  $('.backend').addEventListener('input', localHint);
  $('.test').addEventListener('click', (event) => {
    if (event.isTrusted) void testConnection();
  });
  $('.mode').addEventListener('click', () => {
    selecting = !selecting;
    render();
    if (!selecting) clearMarkers();
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
      const verified = await verifyAndSaveConnection(GM, next, STORAGE);
      config = next;
      $('.backend').value = config.backend;
      render();
      connectionInfo = verified;
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
    if (document.hidden) clearMarkers();
    else {
      routeChanged();
      schedulePositions();
    }
  });
  document.addEventListener('fullscreenchange', schedulePositions);
  if (typeof GM.registerMenuCommand === 'function')
    GM.registerMenuCommand('打开 Treasure Up 选片助手', () => setPanel(true));
  void (async () => {
    const issue = environmentIssue(GM);
    if (issue) {
      updateCurrent();
      render();
      say(issue, 'error');
      return;
    }
    try {
      if (supportedManager(GM.info)) {
        const saved = await GM.getValue(STORAGE, null);
        if (saved && TOKEN.test(saved.token || '')) {
          try {
            config = { backend: normalizeBackend(saved.backend), token: saved.token };
          } catch {
            say('原有服务地址不符合要求，请在连接设置中重新填写。', 'error');
          }
        }
      }
      await draft.refresh();
    } catch {
      say('油猴存储读取失败。请刷新页面；若仍失败，从后台重新安装选片助手后再试。', 'error');
      render();
      return;
    }
    ready = true;
    updateCurrent();
    render();
  })();
})();
