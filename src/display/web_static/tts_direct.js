// 平板直连云 TTS：监听 /ws/audio_out 的 tts_text JSON 帧，
// 平板自己 fetch dashscope 流式 PCM，Web Audio 排队播放。
// 失败时回 tts_failed 给开发板，由开发板走旧 mp3 路径补合成（audio_out.js 播）。
// 决策来源：project_markdown/background03.md
(function () {
  'use strict';

  // 内网信任：API key 直接写在前端（决策 1）
  const API_KEY = "sk-4529e46f796b46539ba4307d5d4fe5c2";
  const URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";
  const MODEL = "qwen3-tts-flash";
  const SAMPLE_RATE = 24000;
  const FETCH_TIMEOUT_MS = 3000; // 首块超时（决策 4：> 3s 触发 fallback）

  const WS_URL =
    (location.protocol === 'https:' ? 'wss://' : 'ws://') +
    location.host + '/ws/audio_out';

  let ws = null;
  let reconnectTimer = null;
  let audioCtx = null;
  let nextStartTime = 0;

  // 段级队列：保证按 segment_id 顺序播放
  // entry: { id, text, voice, ready: Promise<Uint8Array[]>, played: bool, failed: bool }
  const segments = new Map();
  let nextPlayId = 0;

  function log() {
    try { console.log.apply(console, ['[tts_direct]'].concat([].slice.call(arguments))); } catch (_) {}
  }

  function ensureCtx() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
    }
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return audioCtx;
  }

  // autoplay 解锁：首次用户交互时唤醒 AudioContext
  function unlockOnGesture() {
    const handler = () => {
      ensureCtx();
      document.removeEventListener('click', handler);
      document.removeEventListener('touchstart', handler);
    };
    document.addEventListener('click', handler, { once: true });
    document.addEventListener('touchstart', handler, { once: true });
  }
  unlockOnGesture();

  // 段起始 15ms fade-in，抑制 Web Audio 冷启动 / 段首振幅突跳的 click
  // 与 v3 后端 qwen_tts_client 的 fade-in 等价
  const FADE_IN_SAMPLES = Math.floor(SAMPLE_RATE * 0.015);

  function playPcmChunk(pcmU8, fadeIn) {
    const ctx = ensureCtx();
    const samples = pcmU8.length >> 1;
    if (samples <= 0) return;
    const buffer = ctx.createBuffer(1, samples, SAMPLE_RATE);
    const ch = buffer.getChannelData(0);
    const view = new DataView(pcmU8.buffer, pcmU8.byteOffset, pcmU8.byteLength);
    for (let i = 0; i < samples; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
    if (fadeIn) {
      const n = Math.min(FADE_IN_SAMPLES, samples);
      for (let i = 0; i < n; i++) ch[i] *= i / n;
    }
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, nextStartTime);
    src.start(startAt);
    nextStartTime = startAt + buffer.duration;
  }

  function reportFailed(seg, reason) {
    if (seg.failed) return;
    seg.failed = true;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({
        type: 'tts_failed',
        segment_id: seg.id,
        reason,
        text: seg.text,
      }));
      log('已上报 tts_failed seg=', seg.id, 'reason=', reason);
    } catch (e) {
      log('上报 tts_failed 异常', e);
    }
  }

  // 启动 fetch 并把音频块塞进 seg.chunks 队列；播放循环从队列消费
  async function fetchSegment(seg) {
    seg.chunks = [];
    seg.firstChunkResolved = null;
    seg.firstChunkPromise = new Promise((res) => { seg.firstChunkResolved = res; });
    seg.done = false;

    const controller = new AbortController();
    seg.abort = () => controller.abort();

    let resp;
    const t0 = performance.now();
    try {
      resp = await fetch(URL, {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'Authorization': 'Bearer ' + API_KEY,
          'Content-Type': 'application/json',
          'X-DashScope-SSE': 'enable',
        },
        body: JSON.stringify({
          model: MODEL,
          input: { text: seg.text, voice: seg.voice, language_type: 'Chinese' },
        }),
      });
    } catch (e) {
      log('seg=', seg.id, 'fetch 抛异常', e);
      reportFailed(seg, 'fetch_error');
      seg.done = true;
      seg.firstChunkResolved && seg.firstChunkResolved(false);
      return;
    }

    if (!resp.ok) {
      const txt = await resp.text().catch(() => '');
      log('seg=', seg.id, '非200', resp.status, txt.slice(0, 200));
      reportFailed(seg, resp.status >= 500 ? 'http_5xx' : 'http_' + resp.status);
      seg.done = true;
      seg.firstChunkResolved && seg.firstChunkResolved(false);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let firstResolved = false;

    while (true) {
      let chunk;
      try {
        chunk = await reader.read();
      } catch (e) {
        log('seg=', seg.id, 'read 异常', e);
        if (!firstResolved) reportFailed(seg, 'fetch_error');
        break;
      }
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });
      let lineEnd;
      while ((lineEnd = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, lineEnd).trim();
        buf = buf.slice(lineEnd + 1);
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (!data || data === '[DONE]') continue;
        let obj;
        try { obj = JSON.parse(data); } catch { continue; }
        const audioData = obj?.output?.audio?.data;
        if (audioData) {
          const bin = atob(audioData);
          const u8 = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
          seg.chunks.push(u8);
          if (!firstResolved) {
            firstResolved = true;
            log('seg=', seg.id, '首块 PCM 到达', (performance.now() - t0).toFixed(0), 'ms size=', u8.length);
            seg.firstChunkResolved && seg.firstChunkResolved(true);
          }
        }
      }
    }

    seg.done = true;
    if (!firstResolved) {
      seg.firstChunkResolved && seg.firstChunkResolved(false);
    }
    log('seg=', seg.id, 'fetch 流结束 总耗时=', (performance.now() - t0).toFixed(0), 'ms chunks=', seg.chunks.length);
  }

  // 严格按 segment_id 顺序播放：当前 nextPlayId 那段就绪后开始播放
  let playLoopRunning = false;
  async function playLoop() {
    if (playLoopRunning) return;
    playLoopRunning = true;
    try {
      while (true) {
        const seg = segments.get(nextPlayId);
        if (!seg) break; // 等下一个 tts_text

        // 等首块或失败信号
        const ok = await Promise.race([
          seg.firstChunkPromise,
          new Promise((res) => setTimeout(() => res('timeout'), FETCH_TIMEOUT_MS)),
        ]);
        if (ok === 'timeout' && !seg.failed) {
          log('seg=', seg.id, '首块超时', FETCH_TIMEOUT_MS, 'ms');
          reportFailed(seg, 'timeout');
          try { seg.abort && seg.abort(); } catch (_) {}
        }

        if (!seg.failed) {
          // 流式吐 chunk 到 Web Audio：播放循环不能阻塞 fetch 写 chunks
          let i = 0;
          while (true) {
            while (i < seg.chunks.length) {
              // 段首 chunk 加 fade-in，避免冷启动 / 段首振幅突跳的 click
              playPcmChunk(seg.chunks[i], i === 0);
              i++;
            }
            if (seg.done) break;
            await new Promise((res) => setTimeout(res, 20));
          }
        }

        segments.delete(nextPlayId);
        nextPlayId++;
      }
    } finally {
      playLoopRunning = false;
    }
  }

  function onTtsText(msg) {
    const id = msg.segment_id;
    if (typeof id !== 'number') {
      log('tts_text 缺少 segment_id', msg);
      return;
    }
    if (segments.has(id) || id < nextPlayId) {
      log('忽略重复或过期段 id=', id);
      return;
    }
    const seg = {
      id,
      text: msg.text || '',
      voice: msg.voice || 'Cherry',
      failed: false,
      chunks: [],
    };
    segments.set(id, seg);
    log('收到 tts_text seg=', id, '字数=', seg.text.length);
    // 立即发起 fetch（多段并发起 fetch，让网络层 pipeline）
    fetchSegment(seg);
    // 启动播放循环（按 segment_id 顺序）
    playLoop();
  }

  // 起步预热：发一发极小的 dashscope SSE 请求，把 TLS+HTTP/2 握手做掉。
  // 浏览器原生维护连接池，后续真合成可复用同一条 HTTP/2 通道（PoC 实测从 533ms 降到 ~283ms）。
  let warmupDone = false;
  async function warmupFetch() {
    if (warmupDone) return;
    warmupDone = true;
    const ctrl = new AbortController();
    const t0 = performance.now();
    try {
      const r = await fetch(URL, {
        method: 'POST',
        signal: ctrl.signal,
        headers: {
          'Authorization': 'Bearer ' + API_KEY,
          'Content-Type': 'application/json',
          'X-DashScope-SSE': 'enable',
        },
        body: JSON.stringify({
          model: MODEL,
          input: { text: '嗨', voice: 'Cherry', language_type: 'Chinese' },
        }),
      });
      if (!r.ok) {
        log('warmup 非200', r.status);
        return;
      }
      const reader = r.body.getReader();
      // 读到首块就 abort，足够把 TLS+连接握好
      await reader.read();
      ctrl.abort();
      log('warmup 完成', (performance.now() - t0).toFixed(0), 'ms');
    } catch (e) {
      if (e.name !== 'AbortError') log('warmup 异常', e);
    }
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      log('已连接', WS_URL);
      // 页面加载后异步预热一次（不阻塞 UI）
      warmupFetch();
    };
    ws.onmessage = (ev) => {
      // 二进制帧（mp3 fallback 路径）由 audio_out.js 处理，这里只看 JSON
      if (typeof ev.data !== 'string') return;
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg && msg.type === 'tts_text') {
        onTtsText(msg);
      }
    };
    ws.onclose = () => {
      log('断开, 3 秒后重连');
      ws = null;
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000);
    };
    ws.onerror = (e) => log('错误', e);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }

  log('tts_direct 已就绪');
})();
