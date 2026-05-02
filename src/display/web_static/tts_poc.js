// 平板直连 qwen-tts PoC：测端到端首块延迟 + Web Audio 流式播放 PCM。
// 仅用于内网验证，API key 硬编码（决策 1：内网信任）。

const API_KEY = "sk-4529e46f796b46539ba4307d5d4fe5c2";
const URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";
const MODEL = "qwen3-tts-flash";
const VOICE = "Cherry";

const $ = (id) => document.getElementById(id);
const log = $("log");

function logLine(msg, cls = "") {
  const line = document.createElement("div");
  if (cls) line.className = cls;
  line.textContent = msg;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

let audioCtx = null;
let nextStartTime = 0;

function ensureCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function playPcmChunk(pcmU8) {
  const ctx = ensureCtx();
  const samples = pcmU8.length >> 1;
  const buffer = ctx.createBuffer(1, samples, 24000);
  const ch = buffer.getChannelData(0);
  const view = new DataView(pcmU8.buffer, pcmU8.byteOffset, pcmU8.byteLength);
  for (let i = 0; i < samples; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(ctx.destination);
  const startAt = Math.max(ctx.currentTime, nextStartTime);
  src.start(startAt);
  nextStartTime = startAt + buffer.duration;
}

async function runTest() {
  const text = $("text").value.trim();
  if (!text) { logLine("文本为空", "err"); return; }

  $("btn-test").disabled = true;
  $("s-headers").textContent = "-";
  $("s-first").textContent = "-";
  $("s-end").textContent = "-";
  ensureCtx();
  nextStartTime = 0;

  const t0 = performance.now();
  logLine(`text 字数=${text.length}  voice=${VOICE}  model=${MODEL}`);
  logLine(`POST ${URL}`);

  let resp;
  try {
    resp = await fetch(URL, {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + API_KEY,
        "Content-Type": "application/json",
        "X-DashScope-SSE": "enable",
      },
      body: JSON.stringify({
        model: MODEL,
        input: { text, voice: VOICE, language_type: "Chinese" },
      }),
    });
  } catch (e) {
    logLine(`fetch 抛异常: ${e.message}`, "err");
    logLine("如果是 CORS / TypeError，说明浏览器拦了跨域。需要走 Kotlin 原生 fetch 绕过。", "warn");
    $("btn-test").disabled = false;
    return;
  }

  const tHeaders = performance.now();
  const headersMs = (tHeaders - t0).toFixed(0);
  $("s-headers").textContent = headersMs + " ms";
  logLine(`[t=${headersMs}ms] HTTP响应头到达  status=${resp.status}`);
  if (!resp.ok) {
    const txt = await resp.text();
    logLine(`!! 非200: ${txt.slice(0, 500)}`, "err");
    $("btn-test").disabled = false;
    return;
  }

  let firstChunkT = null;
  let totalBytes = 0;
  let chunkCount = 0;
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let lineEnd;
    while ((lineEnd = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, lineEnd).trim();
      buf = buf.slice(lineEnd + 1);
      if (!line.startsWith("data:")) continue;
      const data = line.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      let obj;
      try { obj = JSON.parse(data); } catch { continue; }
      const audioData = obj?.output?.audio?.data;
      if (audioData) {
        const bin = atob(audioData);
        const u8 = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
        if (firstChunkT === null) {
          firstChunkT = performance.now();
          const ms = (firstChunkT - t0).toFixed(0);
          $("s-first").textContent = ms + " ms";
          logLine(`[t=${ms}ms] ★ 首块 PCM 到达  size=${u8.length}B`, "ok");
        }
        totalBytes += u8.length;
        chunkCount++;
        playPcmChunk(u8);
      }
      if (obj?.output?.finish_reason === "stop") break;
    }
  }

  const tEnd = performance.now();
  const endMs = (tEnd - t0).toFixed(0);
  $("s-end").textContent = endMs + " ms";
  logLine("");
  logLine("=".repeat(50));
  logLine(`首块延迟        : ${(firstChunkT - t0).toFixed(0)} ms`);
  logLine(`全部合成完成    : ${endMs} ms`);
  logLine(`总PCM大小       : ${(totalBytes / 1024).toFixed(1)} KB (${chunkCount} chunks)`);
  logLine("=".repeat(50));
  logLine("对比基线: 当前架构端到端 ~590-680ms。这次首块若 < 530ms 改造就有收益。", "warn");

  $("btn-test").disabled = false;
}

$("btn-test").addEventListener("click", runTest);
$("btn-clear").addEventListener("click", () => { log.innerHTML = ""; });
logLine("PoC 就绪。点击「测试 qwen-tts」开始。", "ok");
logLine("注意: 第一次点击会有 AudioContext 启动开销（首次 +20-50ms），第二次起才是真实数字。", "warn");
