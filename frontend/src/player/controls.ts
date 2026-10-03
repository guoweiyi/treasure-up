import type Artplayer from 'artplayer';

interface Actions {
  openSettings: () => void;
  toggleDanmaku: () => void;
  setVolume: (value: number) => void;
  getVolume: () => number;
}

/** Keyboard commands apply only while focus is inside this player. */
export function installPlayerControls(art: Artplayer, actions: Actions) {
  const root = art.template.$player;
  root.tabIndex = 0;
  root.setAttribute('role', 'group');
  root.setAttribute('aria-label', '视频播放器');
  const settings = document.createElement('button');
  settings.type = 'button';
  settings.className = 'player-control-button';
  settings.textContent = '设置';
  settings.setAttribute('aria-label', '打开播放器设置');
  settings.setAttribute('aria-haspopup', 'dialog');
  settings.setAttribute('aria-expanded', 'false');
  const danmaku = document.createElement('button');
  danmaku.type = 'button';
  danmaku.className = 'player-control-button player-danmaku-toggle';
  danmaku.textContent = '弹幕';
  danmaku.setAttribute('aria-label', '显示弹幕');
  art.controls.add({
    name: 'treasure-danmaku',
    position: 'right',
    index: 5,
    html: danmaku,
    click: actions.toggleDanmaku,
  });
  art.controls.add({
    name: 'treasure-settings',
    position: 'right',
    index: 6,
    html: settings,
    click: actions.openSettings,
  });
  for (const [name, label] of [
    ['playAndPause', '播放或暂停'],
    ['volume', '静音或取消静音'],
    ['pip', '画中画'],
    ['fullscreen', '进入或退出全屏'],
    ['fullscreenWeb', '进入或退出网页全屏'],
  ]) {
    const element = root.querySelector<HTMLElement>(`.art-control-${name}`);
    if (!element) continue;
    element.tabIndex = 0;
    element.setAttribute('role', 'button');
    element.setAttribute('aria-label', label);
    element.addEventListener('keydown', (event) => {
      if (event.target !== element || !['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      event.stopPropagation();
      element.click();
    });
  }
  const progress = art.template.$progress;
  progress.tabIndex = 0;
  progress.setAttribute('role', 'slider');
  progress.setAttribute('aria-label', '播放进度');
  progress.setAttribute('aria-valuemin', '0');
  art.on('video:timeupdate', () => {
    progress.setAttribute('aria-valuemax', String(Math.round(art.duration || 0)));
    progress.setAttribute('aria-valuenow', String(Math.round(art.currentTime || 0)));
    progress.setAttribute(
      'aria-valuetext',
      `已播放 ${Math.round(art.currentTime || 0)} 秒，共 ${Math.round(art.duration || 0)} 秒`,
    );
  });
  root.addEventListener('keydown', (event) => {
    if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return;
    const target = event.target as HTMLElement;
    if (
      target.closest(
        'button, input, select, textarea, summary, [role="dialog"], [contenteditable="true"]',
      )
    )
      return;
    const key = event.key.toLowerCase();
    if (
      ![' ', 'k', 'arrowleft', 'arrowright', 'arrowup', 'arrowdown', 'm', 'd', 's', 'f'].includes(
        key,
      )
    )
      return;
    event.preventDefault();
    event.stopPropagation();
    if (key === ' ' || key === 'k') art.toggle();
    if (key === 'arrowleft' || key === 'arrowright')
      art.currentTime = Math.min(
        art.duration || 0,
        Math.max(0, art.currentTime + (key === 'arrowleft' ? -5 : 5)),
      );
    if (key === 'arrowup' || key === 'arrowdown')
      actions.setVolume(
        Math.max(0, Math.min(1, actions.getVolume() + (key === 'arrowup' ? 0.05 : -0.05))),
      );
    if (key === 'm') art.muted = !art.muted;
    if (key === 'd') actions.toggleDanmaku();
    if (key === 's') actions.openSettings();
    if (key === 'f') art.fullscreenWeb = !art.fullscreenWeb;
  });
  root.addEventListener('pointerdown', (event) => {
    if (
      (event.target as HTMLElement).closest(
        'button, input, select, [tabindex], [role="dialog"]',
      ) === root
    )
      root.focus({ preventScroll: true });
  });
  return { settings, danmaku };
}
