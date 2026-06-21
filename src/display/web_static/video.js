/**
 * video.js — 摄像头标注画面 WebSocket 客户端
 * 连接 /ws/video，接收 JPEG 帧并渲染到浮窗 <img>
 */
(function () {
  'use strict';

  const pip = document.getElementById('video-pip');
  const feed = document.getElementById('video-feed');
  const btnClose = document.getElementById('btn-video-close');
  const placeholder = document.getElementById('video-placeholder');
  const btnToggle = document.getElementById('btn-video-toggle');
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
    if (btnToggle) btnToggle.classList.add('active');   // 触发按钮状态同步
  }

  function hide() {
    if (visible) {
      pip.setAttribute('aria-hidden', 'true');
      pip.classList.remove('active');
      visible = false;
    }
    if (btnToggle) btnToggle.classList.remove('active');  // × 关闭后按钮也复位
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
        if (placeholder) placeholder.style.display = 'none';  // 有帧了，隐藏占位
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

  // 调 /api/camera_enable 开/关无人机相机推流（平时关省 CPU，要看时再开）
  function setCamera(enable) {
    fetch('/api/camera_enable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enable: enable }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) { console.log('[video] camera_enable=%s ->', enable, d); })
      .catch(function (e) { console.warn('[video] camera_enable 请求失败:', e); });
  }

  // 关闭按钮：关浮窗 + 关推流
  if (btnClose) {
    btnClose.addEventListener('click', function () {
      disconnect();
      setCamera(false);
    });
  }

  // 手动触发按钮：开浮窗 = 开推流，关浮窗 = 关推流
  if (btnToggle) {
    btnToggle.addEventListener('click', function () {
      if (visible) {
        hide();
        setCamera(false);
      } else {
        show();
        connect();            // 重新打开时确保在尝试连流
        setCamera(true);
      }
    });
  }

  // 页面卸载时清理
  window.addEventListener('beforeunload', function () {
    disconnect();
  });

  // 启动连接
  connect();
})();
