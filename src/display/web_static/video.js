/**
 * video.js — 摄像头标注画面 WebSocket 客户端
 * 连接 /ws/video，接收 JPEG 帧并渲染到浮窗 <img>
 */
(function () {
  'use strict';

  const pip = document.getElementById('video-pip');
  const feed = document.getElementById('video-feed');
  const btnClose = document.getElementById('btn-video-close');
  let ws = null;
  let reconnectTimer = null;
  let reconnectDelay = 2000;
  let visible = false;

  function show() {
    if (!visible) {
      pip.setAttribute('aria-hidden', 'false');
      pip.classList.add('active');
      visible = true;
    }
  }

  function hide() {
    if (visible) {
      pip.setAttribute('aria-hidden', 'true');
      pip.classList.remove('active');
      visible = false;
    }
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = proto + '//' + location.host + '/ws/video';
    ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';

    ws.onopen = function () {
      console.log('[video] WebSocket 已连接 /ws/video');
      reconnectDelay = 2000;
    };

    ws.onmessage = function (event) {
      try {
        const blob = new Blob([event.data], { type: 'image/jpeg' });
        const url = URL.createObjectURL(blob);
        const prev = feed.src;
        feed.src = url;
        if (prev && prev.startsWith('blob:')) {
          URL.revokeObjectURL(prev);
        }
        show();
      } catch (e) {
        console.warn('[video] 帧解析失败:', e);
      }
    };

    ws.onclose = function () {
      console.log('[video] WebSocket 已断开，%d ms 后重连', reconnectDelay);
      ws = null;
      scheduleReconnect();
    };

    ws.onerror = function (e) {
      console.warn('[video] WebSocket 错误');
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      reconnectDelay = Math.min(reconnectDelay * 1.5, 30000);
      connect();
    }, reconnectDelay);
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.onclose = null;
      ws.onerror = null;
      ws.close();
      ws = null;
    }
    hide();
  }

  // 关闭按钮
  if (btnClose) {
    btnClose.addEventListener('click', function () {
      disconnect();
    });
  }

  // 页面卸载时清理
  window.addEventListener('beforeunload', function () {
    disconnect();
  });

  // 启动连接
  connect();
})();
