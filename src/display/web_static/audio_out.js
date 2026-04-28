// 接 /ws/audio_out 收 mp3，本地播放（平板 / 浏览器都生效）
(function () {
  'use strict';

  const WS_URL =
    (location.protocol === 'https:' ? 'wss://' : 'ws://') +
    location.host + '/ws/audio_out';

  let ws = null;
  let reconnectTimer = null;
  // 排队播放，避免后到的 mp3 被前一段截断
  const queue = [];
  let playing = false;
  let currentUrl = null;

  function log() {
    try { console.log.apply(console, ['[audio_out]'].concat([].slice.call(arguments))); } catch (_) {}
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    ws = new WebSocket(WS_URL);
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => log('已连接');
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') return; // 忽略文本帧
      enqueue(ev.data);
    };
    ws.onclose = () => {
      log('断开, 3 秒后重连');
      ws = null;
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000);
    };
    ws.onerror = (e) => log('错误', e);
  }

  function enqueue(arrayBuffer) {
    const blob = new Blob([arrayBuffer], { type: 'audio/mpeg' });
    queue.push(blob);
    log('入队 mp3, 字节=', arrayBuffer.byteLength, '队列长度=', queue.length);
    if (!playing) playNext();
  }

  function playNext() {
    if (queue.length === 0) {
      playing = false;
      return;
    }
    playing = true;
    const blob = queue.shift();
    if (currentUrl) {
      URL.revokeObjectURL(currentUrl);
      currentUrl = null;
    }
    currentUrl = URL.createObjectURL(blob);
    const audio = new Audio(currentUrl);
    audio.onended = () => {
      log('播放结束');
      playNext();
    };
    audio.onerror = (e) => {
      log('播放出错', e);
      playNext();
    };
    const p = audio.play();
    if (p && typeof p.catch === 'function') {
      p.catch(err => {
        log('autoplay 被拒, 等待用户交互后重试:', err);
        // autoplay 失败：等用户在页面上点一下就能恢复
        const resume = () => {
          document.removeEventListener('click', resume);
          document.removeEventListener('touchstart', resume);
          audio.play().catch(e2 => log('再次尝试失败', e2));
        };
        document.addEventListener('click', resume, { once: true });
        document.addEventListener('touchstart', resume, { once: true });
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }

  log('audio_out 已就绪');
})();
