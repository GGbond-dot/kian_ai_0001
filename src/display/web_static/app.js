/**
 * AI Agent Console — Web UI 客户端
 * WebSocket 连接管理 + UI 状态驱动
 */

(function () {
  'use strict';

  // ===================== DOM 引用 =====================
  const $ = (sel) => document.querySelector(sel);
  const app          = $('#app');
  const titleDot     = $('.title-dot');
  const titleSub     = $('.title-sub');
  const connBadge    = $('.conn-badge');
  const statusCapsule = $('.status-capsule-label');
  const statusMain   = $('.status-main-text');
  const statusBars   = $('.audio-bars');
  const stageBars    = $('.stage-bars');
  const stageLabel   = $('.stage-label');
  const emotionEl    = $('.emotion-display');
  const transcriptBody = $('.transcript-body');
  const transcriptCard = $('.transcript-card');
  const btnManual    = $('#btn-manual');
  const btnAuto      = $('#btn-auto');
  const btnAbort     = $('#btn-abort');
  const btnMode      = $('#btn-mode');
  const btnSend      = $('#btn-send');
  const textInput    = $('#text-input');
  const btnFullscreen = $('#btn-fullscreen');

  // ===================== 全屏 =====================
  // 华为浏览器/微信内置浏览器都不认 PWA display:fullscreen，
  // 提供一个手动全屏按钮，用 Fullscreen API 临时藏掉地址栏。
  if (btnFullscreen) {
    btnFullscreen.addEventListener('click', async () => {
      const root = document.documentElement;
      const isFs = document.fullscreenElement || document.webkitFullscreenElement;
      try {
        if (isFs) {
          if (document.exitFullscreen) await document.exitFullscreen();
          else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
        } else {
          if (root.requestFullscreen) await root.requestFullscreen();
          else if (root.webkitRequestFullscreen) root.webkitRequestFullscreen();
          else alert('当前浏览器不支持全屏 API，建议改用 Chrome');
        }
      } catch (e) {
        console.warn('全屏切换失败:', e);
      }
    });
  }

  // ===================== 状态 =====================
  let ws = null;
  let reconnectTimer = null;
  let autoMode = false;
  let currentState = 'idle'; // idle | listening | speaking | offline

  // ===================== WebSocket =====================
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
        const msg = JSON.parse(e.data);
        handleMessage(msg);
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
        // 完整状态快照 (新连接时)
        if (msg.status !== undefined)   updateStatus(msg.status, msg.connected);
        if (msg.text !== undefined)     updateText(msg.text);
        if (msg.emotion !== undefined)  updateEmotion(msg.emotion);
        if (msg.auto_mode !== undefined) setAutoMode(msg.auto_mode);
        if (msg.button_text !== undefined) updateButtonText(msg.button_text);
        break;

      case 'status':
        updateStatus(msg.status, msg.connected);
        break;

      case 'text':
        updateText(msg.text);
        break;

      case 'emotion':
        updateEmotion(msg.emotion);
        break;

      case 'button':
        updateButtonText(msg.text);
        break;

      case 'auto_mode':
        setAutoMode(msg.value);
        break;
    }
  }

  // ===================== UI 更新 =====================
  function setConnected(connected) {
    if (connected) {
      connBadge.textContent = 'ONLINE';
      connBadge.classList.remove('disconnected');
    } else {
      connBadge.textContent = 'OFFLINE';
      connBadge.classList.add('disconnected');
      setState('offline');
    }
  }

  function updateStatus(status, connected) {
    statusMain.textContent = `状态: ${status}`;

    // 根据状态文本判断当前状态
    const s = status.toLowerCase();
    if (s.includes('聆听') || s.includes('listening')) {
      setState('listening');
    } else if (s.includes('说话') || s.includes('speaking')) {
      setState('speaking');
    } else if (s.includes('未连接') || s.includes('offline')) {
      setState('offline');
    } else {
      setState('idle');
    }
  }

  function setState(state) {
    currentState = state;
    app.setAttribute('data-state', state);

    const isActive = (state === 'listening' || state === 'speaking');

    // 标题栏指示灯
    titleDot.classList.toggle('active', isActive);

    // 状态胶囊标签
    const capsuleMap = {
      speaking:  'VOICE OUTPUT',
      listening: 'VOICE INPUT',
      offline:   'OFFLINE',
      idle:      'STANDBY'
    };
    statusCapsule.textContent = capsuleMap[state] || 'STANDBY';
    titleSub.textContent = capsuleMap[state] || 'STANDBY';

    // 音频频谱条动画
    statusBars.classList.toggle('active', isActive);
    stageBars.classList.toggle('active', isActive);

    // 中央标签
    const labelMap = {
      speaking:  'RESPONDING',
      listening: 'LISTENING',
      idle:      'READY',
      offline:   'OFFLINE'
    };
    stageLabel.textContent = labelMap[state] || 'READY';

    // 状态发光闪烁
    const glow = $('.status-glow');
    if (glow) {
      glow.style.opacity = '0.55';
      setTimeout(() => { glow.style.opacity = '0.18'; }, 360);
    }
  }

  function updateText(text) {
    if (!text || !text.trim()) return;
    transcriptBody.textContent = text;
    // 自动滚动到底部
    transcriptBody.scrollTop = transcriptBody.scrollHeight;
    // 闪烁动画
    transcriptCard.classList.add('flash');
    setTimeout(() => transcriptCard.classList.remove('flash'), 180);
  }

  function updateEmotion(emotion) {
    if (!emotion) return;

    // 弹跳动画
    const core = $('.core-shell');
    core.classList.add('pop');
    setTimeout(() => core.classList.remove('pop'), 300);

    // 判断是 emoji 还是图片路径
    if (emotion.includes('/') || emotion.includes('.')) {
      // 图片 (通过 /emojis/ 路由提供)
      const name = emotion.replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '');
      emotionEl.innerHTML = `<img src="/emojis/${name}.gif" alt="${name}" onerror="this.parentElement.innerHTML='<span class=emoji>&#x1F60A;</span>'">`;
    } else {
      // Emoji 字符
      emotionEl.innerHTML = `<span class="emoji">${emotion || '&#x1F60A;'}</span>`;
    }
  }

  function updateButtonText(text) {
    if (btnAuto) btnAuto.textContent = text || '开始对话';
  }

  function setAutoMode(isAuto) {
    autoMode = isAuto;
    btnManual.style.display = isAuto ? 'none' : '';
    btnAuto.style.display = isAuto ? '' : 'none';
    btnMode.textContent = isAuto ? '自动对话' : '手动对话';
  }

  // ===================== 用户操作 =====================

  // 手动模式 - 按住说话
  let isManualPressed = false;

  function onManualStart(e) {
    e.preventDefault();
    if (isManualPressed) return;
    isManualPressed = true;
    btnManual.textContent = '松开以停止';
    send({ action: 'press' });
  }

  function onManualEnd(e) {
    e.preventDefault();
    if (!isManualPressed) return;
    isManualPressed = false;
    btnManual.textContent = '按住后说话';
    send({ action: 'release' });
  }

  // 触摸和鼠标事件
  btnManual.addEventListener('mousedown', onManualStart);
  btnManual.addEventListener('mouseup', onManualEnd);
  btnManual.addEventListener('mouseleave', onManualEnd);
  btnManual.addEventListener('touchstart', onManualStart, { passive: false });
  btnManual.addEventListener('touchend', onManualEnd, { passive: false });
  btnManual.addEventListener('touchcancel', onManualEnd, { passive: false });

  // 自动模式
  btnAuto.addEventListener('click', () => send({ action: 'auto' }));

  // 中断
  btnAbort.addEventListener('click', () => send({ action: 'abort' }));

  // 模式切换
  btnMode.addEventListener('click', () => send({ action: 'mode' }));

  // 发送文本
  function sendText() {
    const text = textInput.value.trim();
    if (!text) return;
    send({ action: 'send_text', text });
    textInput.value = '';
  }

  btnSend.addEventListener('click', sendText);
  textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      sendText();
    }
  });

  // ===================== 初始化 =====================
  setAutoMode(false);
  connect();

})();
