// 平板麦克风桥接 —— 仅在 Android WebView (window.AudioBridge 存在) 下生效
// 浏览器打开同一页面时此模块自动 no-op，不影响开发板本地麦克风流程。
(function () {
  'use strict';

  if (!window.AudioBridge || typeof window.AudioBridge.start !== 'function') {
    return; // 普通浏览器环境
  }

  const WS_URL =
    (location.protocol === 'https:' ? 'wss://' : 'ws://') +
    location.host + '/ws/audio_in';

  let ws = null;
  let wantOpen = false;
  let firstChunkSent = false;

  function log() {
    try { console.log.apply(console, ['[tablet_mic]'].concat([].slice.call(arguments))); } catch (_) {}
  }

  function ensureWs() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return ws;
    }
    ws = new WebSocket(WS_URL);
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      log('audio_in WS 连接成功');
      try {
        ws.send(JSON.stringify({
          sample_rate: window.AudioBridge.sampleRate ? window.AudioBridge.sampleRate() : 16000,
          frame_ms: 20,
          source: 'android_webview_tablet'
        }));
      } catch (_) {}
    };
    ws.onclose = () => { log('audio_in WS 关闭'); ws = null; };
    ws.onerror = (e) => log('audio_in WS 错误', e);
    return ws;
  }

  function base64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // Kotlin 端会调用这两个方法
  window.AudioBridgeJS = {
    onPcmChunk: function (b64, capturedAtMs) {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const pcm = base64ToBytes(b64);
      const buf = new ArrayBuffer(8 + pcm.length);
      const view = new DataView(buf);
      // 64 位时间戳，小端，分高低 32 位写
      const ts = BigInt(capturedAtMs);
      view.setBigUint64(0, ts, true);
      new Uint8Array(buf, 8).set(pcm);
      try {
        ws.send(buf);
        if (!firstChunkSent) {
          firstChunkSent = true;
          log('首帧已发送, 字节=', pcm.length, 'capturedAt=', capturedAtMs);
        }
      } catch (e) {
        log('发送失败', e);
      }
    },
    onEvent: function (name, detail) {
      log('AudioBridge event', name, detail);
      if (name === 'started') firstChunkSent = false;
      if (name === 'stopped' && ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'stopped' })); } catch (_) {}
      }
    }
  };

  // 把"按住说话"按钮的按下/松开同步到原生录音
  function hookManual() {
    const buttons = document.querySelectorAll('[data-manual-button]');
    if (!buttons.length) {
      log('未找到 [data-manual-button], 100ms 后重试');
      setTimeout(hookManual, 100);
      return;
    }
    const start = () => { wantOpen = true; ensureWs(); window.AudioBridge.start(); };
    const stop = () => { if (!wantOpen) return; wantOpen = false; window.AudioBridge.stop(); };
    buttons.forEach((btn) => {
      btn.addEventListener('mousedown', start);
      btn.addEventListener('touchstart', start, { passive: false });
      btn.addEventListener('mouseup', stop);
      btn.addEventListener('mouseleave', stop);
      btn.addEventListener('touchend', stop, { passive: false });
      btn.addEventListener('touchcancel', stop, { passive: false });
    });
    log('已绑定到按钮数量', buttons.length);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', hookManual);
  } else {
    hookManual();
  }

  log('tablet_mic 已就绪, sampleRate=', window.AudioBridge.sampleRate ? window.AudioBridge.sampleRate() : '?');
})();
