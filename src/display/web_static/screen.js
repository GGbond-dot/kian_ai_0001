/**
 * 开发板随机屏幕 — 表情/状态面板客户端
 *
 * 只连 /ws 控制信令:表情/状态/文本展示 + 触摸指令(press/release/abort/mode/auto)。
 * 不连 /ws/audio_out、/ws/audio_in,不注册 Service Worker(音频仍走本机/平板)。
 *
 * 调试参数:
 *   ?flip=0    关闭 180° 旋转(屏幕物理安装颠倒,默认旋转)
 *   ?gif=1     表情用动态 GIF(默认静态首帧 — 板上软件合成,持续解码压力大)
 *   ?stress=1  每 2s 轮换一个表情,用于真机渲染压测
 */

(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const screenEl   = $('#screen');
  const connText   = $('.conn-text');
  const statusText = $('.status-text');
  const emotionImg = $('#emotion-img');
  const emotionCanvas = $('#emotion-canvas');
  const speechText = $('#speech-text');
  const btnTalk    = $('#btn-talk');
  const btnAuto    = $('#btn-auto');
  const btnAbort   = $('#btn-abort');
  const btnMode    = $('#btn-mode');

  const params = new URLSearchParams(location.search);

  // ===================== 180° 翻转 =====================
  if (params.get('flip') !== '0') {
    screenEl.classList.add('flipped');
  }

  // ===================== WebSocket =====================
  let ws = null;
  let reconnectTimer = null;

  function connect() {
    if (ws && ws.readyState <= 1) return;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
      clearTimeout(reconnectTimer);
      setConnected(true);
    };

    ws.onclose = () => {
      setConnected(false);
      scheduleReconnect();
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (e) => {
      try {
        handleMessage(JSON.parse(e.data));
      } catch (err) {
        console.warn('消息解析失败:', err);
      }
    };
  }

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 3000);
  }

  function send(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }

  // ===================== 消息处理 =====================
  function handleMessage(msg) {
    switch (msg.type) {
      case 'snapshot':
        if (msg.status !== undefined)      updateStatus(msg.status);
        if (msg.text !== undefined)        updateText(msg.text);
        if (msg.emotion !== undefined)     updateEmotion(msg.emotion);
        if (msg.auto_mode !== undefined)   setAutoMode(msg.auto_mode);
        if (msg.button_text !== undefined) updateButtonText(msg.button_text);
        break;
      case 'status':    updateStatus(msg.status); break;
      case 'text':      updateText(msg.text); break;
      case 'emotion':   updateEmotion(msg.emotion); break;
      case 'button':    updateButtonText(msg.text); break;
      case 'auto_mode': setAutoMode(msg.value); break;
    }
  }

  // ===================== UI 更新 =====================
  function setConnected(connected) {
    screenEl.classList.toggle('online', connected);
    connText.textContent = connected ? '在线' : '离线';
    if (!connected) screenEl.setAttribute('data-state', 'offline');
  }

  function updateStatus(status) {
    statusText.textContent = `状态: ${status}`;
    const s = (status || '').toLowerCase();
    if (s.includes('聆听') || s.includes('listening')) {
      screenEl.setAttribute('data-state', 'listening');
    } else if (s.includes('说话') || s.includes('speaking')) {
      screenEl.setAttribute('data-state', 'speaking');
    } else if (s.includes('未连接') || s.includes('offline')) {
      screenEl.setAttribute('data-state', 'offline');
    } else {
      screenEl.setAttribute('data-state', 'idle');
    }
  }

  function updateText(text) {
    if (!text || !text.trim()) return;
    speechText.textContent = text;
  }

  // 默认静态首帧:GIF 持续解码 + 软件合成会吃满板子 CPU,?gif=1 才放开动图
  const useGif = params.get('gif') === '1';

  function updateEmotion(emotion) {
    const name = String(emotion || '').trim().toLowerCase();
    if (!name) return;
    const url = `/emojis/${encodeURIComponent(name)}.gif`;
    if (useGif) {
      emotionImg.src = url;
      return;
    }
    drawStaticFrame(url);
  }

  // 把 GIF 首帧画进 canvas,之后没有任何持续渲染开销。
  // 按"实际显示尺寸 × 设备像素比"高质量重采样,源图 240px 放大也不糊
  function drawStaticFrame(url, isFallback) {
    const tmp = new Image();
    tmp.onload = () => {
      const halo = emotionCanvas.parentElement.getBoundingClientRect();
      const cssSize = Math.max(halo.width * 0.76, tmp.naturalWidth);
      const px = Math.round(cssSize * (window.devicePixelRatio || 1));
      emotionCanvas.width = px;
      emotionCanvas.height = px;
      const ctx = emotionCanvas.getContext('2d');
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(tmp, 0, 0, px, px);
      emotionImg.style.display = 'none';
      emotionCanvas.style.display = '';
    };
    tmp.onerror = () => {
      if (!isFallback) drawStaticFrame('/emojis/neutral.gif', true);
    };
    tmp.src = url;
  }

  // 未知表情名/文件缺失 → 回退 neutral(动图模式)
  emotionImg.addEventListener('error', () => {
    if (!emotionImg.src.endsWith('/emojis/neutral.gif')) {
      emotionImg.src = '/emojis/neutral.gif';
    }
  });

  if (!useGif) drawStaticFrame('/emojis/neutral.gif');

  function updateButtonText(text) {
    btnAuto.textContent = text || '开始对话';
  }

  function setAutoMode(isAuto) {
    btnTalk.style.display = isAuto ? 'none' : '';
    btnAuto.style.display = isAuto ? '' : 'none';
    btnMode.textContent = isAuto ? '自动对话' : '手动对话';
  }

  // ===================== 按住说话 =====================
  let isPressed = false;

  function onTalkStart(e) {
    e.preventDefault();
    if (isPressed) return;
    isPressed = true;
    btnTalk.classList.add('pressed');
    btnTalk.textContent = '松开以停止';
    send({ action: 'press' });
  }

  function onTalkEnd(e) {
    e.preventDefault();
    if (!isPressed) return;
    isPressed = false;
    btnTalk.classList.remove('pressed');
    btnTalk.textContent = '按住后说话';
    send({ action: 'release' });
  }

  btnTalk.addEventListener('mousedown', onTalkStart);
  btnTalk.addEventListener('mouseup', onTalkEnd);
  btnTalk.addEventListener('mouseleave', onTalkEnd);
  btnTalk.addEventListener('touchstart', onTalkStart, { passive: false });
  btnTalk.addEventListener('touchend', onTalkEnd, { passive: false });
  btnTalk.addEventListener('touchcancel', onTalkEnd, { passive: false });

  btnAuto.addEventListener('click', () => send({ action: 'auto' }));
  btnAbort.addEventListener('click', () => send({ action: 'abort' }));
  btnMode.addEventListener('click', () => send({ action: 'mode' }));

  // ===================== 屏幕常亮 =====================
  // Wake Lock 仅安全上下文可用;kiosk 走 http://localhost 满足条件
  let wakeLock = null;

  async function requestWakeLock() {
    if (!('wakeLock' in navigator)) {
      console.warn('当前浏览器不支持 Wake Lock,常亮依赖系统级 xset/电源设置');
      return;
    }
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } catch (e) {
      console.warn('WakeLock 获取失败:', e);
    }
  }

  // 页面重新可见时(浏览器最小化/切回)需要重新申请
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !wakeLock) {
      requestWakeLock();
    }
  });

  requestWakeLock();

  // ===================== 渲染压测 =====================
  if (params.get('stress') === '1') {
    const emotions = [
      'angry', 'confident', 'confused', 'cool', 'crying', 'delicious',
      'embarrassed', 'funny', 'happy', 'kissy', 'laughing', 'loving',
      'neutral', 'relaxed', 'sad', 'shocked', 'silly', 'sleepy',
      'surprised', 'thinking', 'winking',
    ];
    let i = 0;
    setInterval(() => {
      updateEmotion(emotions[i % emotions.length]);
      i += 1;
    }, 2000);
  }

  // ===================== 初始化 =====================
  setAutoMode(false);
  connect();

})();
