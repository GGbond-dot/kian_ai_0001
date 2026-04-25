"""
Web UI 延迟测试工具

开发板运行: python test_web_latency.py
平板浏览器访问: http://<开发板IP>:8080

测试内容:
  1. WebSocket 往返延迟 (ping-pong)
  2. 状态推送延迟模拟 (模拟 IDLE -> LISTENING -> SPEAKING 状态切换)
  3. 连续快速推送压力测试
"""

import asyncio
import json
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Web UI 延迟测试</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #040814; color: #e0e0e0;
    font-family: -apple-system, system-ui, sans-serif;
    padding: 20px; min-height: 100vh;
  }
  h1 { color: #68a8ff; margin-bottom: 20px; font-size: 1.4em; }
  .section {
    background: #0d1525; border-radius: 12px;
    padding: 16px; margin-bottom: 16px;
    border: 1px solid #1a2640;
  }
  .section h2 { color: #26d8c7; font-size: 1.1em; margin-bottom: 12px; }
  .result {
    font-family: monospace; font-size: 14px;
    line-height: 1.8; white-space: pre-wrap;
  }
  .good { color: #4ade80; }
  .warn { color: #fbbf24; }
  .bad  { color: #f87171; }
  button {
    background: #1a2640; color: #68a8ff; border: 1px solid #2a3a5c;
    padding: 10px 20px; border-radius: 8px; font-size: 14px;
    cursor: pointer; margin: 4px;
  }
  button:active { background: #2a3a5c; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .status-bar {
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 16px; font-size: 14px;
  }
  .dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: #f87171;
  }
  .dot.connected { background: #4ade80; }
  .state-display {
    font-size: 24px; text-align: center; padding: 20px;
    border-radius: 8px; background: #112544;
    transition: all 0.15s ease;
  }
</style>
</head>
<body>
<h1>Web UI 延迟测试</h1>

<div class="status-bar">
  <div class="dot" id="dot"></div>
  <span id="connStatus">未连接</span>
</div>

<!-- 测试1: ping-pong -->
<div class="section">
  <h2>1. WebSocket 往返延迟</h2>
  <button onclick="runPingTest()" id="btnPing">开始测试 (20次)</button>
  <div class="result" id="pingResult"></div>
</div>

<!-- 测试2: 状态推送 -->
<div class="section">
  <h2>2. 状态推送延迟</h2>
  <button onclick="runStateTest()" id="btnState">开始测试</button>
  <div class="state-display" id="stateDisplay">等待测试...</div>
  <div class="result" id="stateResult"></div>
</div>

<!-- 测试3: 压力测试 -->
<div class="section">
  <h2>3. 连续快速推送 (100条消息)</h2>
  <button onclick="runBurstTest()" id="btnBurst">开始测试</button>
  <div class="result" id="burstResult"></div>
</div>

<script>
let ws = null;
let pingTimes = {};
let stateReceiveTimes = [];
let burstState = { expected: 0, received: 0, startTime: 0, latencies: [] };

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    document.getElementById('dot').classList.add('connected');
    document.getElementById('connStatus').textContent = '已连接';
  };
  ws.onclose = () => {
    document.getElementById('dot').classList.remove('connected');
    document.getElementById('connStatus').textContent = '已断开，3秒后重连...';
    setTimeout(connect, 3000);
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    handleMessage(msg);
  };
}

function handleMessage(msg) {
  if (msg.type === 'pong') {
    const sent = pingTimes[msg.id];
    if (sent) {
      const rtt = performance.now() - sent;
      delete pingTimes[msg.id];
      addPingResult(msg.id, rtt);
    }
  } else if (msg.type === 'state_change') {
    const now = performance.now();
    const serverTs = msg.server_ts;
    const el = document.getElementById('stateDisplay');
    const colors = { idle: '#68a8ff', listening: '#26d8c7', speaking: '#ff8a5b' };
    const labels = { idle: '待命 STANDBY', listening: '聆听中 LISTENING', speaking: '说话中 SPEAKING' };
    el.textContent = labels[msg.state] || msg.state;
    el.style.background = colors[msg.state] || '#112544';
    el.style.color = '#040814';
    stateReceiveTimes.push({ state: msg.state, rtt: now - msg.browser_ts, serverTs });
  } else if (msg.type === 'burst') {
    const now = performance.now();
    burstState.received++;
    burstState.latencies.push(now - msg.browser_ts);
    if (burstState.received >= burstState.expected) {
      showBurstResult();
    }
  }
}

// ========== 测试1: Ping-Pong ==========
let pingResults = [];
let pingCount = 0;
const PING_TOTAL = 20;

async function runPingTest() {
  document.getElementById('btnPing').disabled = true;
  document.getElementById('pingResult').textContent = '测试中...';
  pingResults = [];
  pingCount = 0;
  for (let i = 0; i < PING_TOTAL; i++) {
    const id = i + 1;
    pingTimes[id] = performance.now();
    ws.send(JSON.stringify({ action: 'ping', id }));
    await sleep(100);
  }
  // 等最后的 pong 回来
  await sleep(500);
  showPingSummary();
  document.getElementById('btnPing').disabled = false;
}

function addPingResult(id, rtt) {
  pingResults.push(rtt);
}

function showPingSummary() {
  if (pingResults.length === 0) {
    document.getElementById('pingResult').textContent = '未收到任何回复';
    return;
  }
  const sorted = [...pingResults].sort((a, b) => a - b);
  const avg = sorted.reduce((a, b) => a + b, 0) / sorted.length;
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  const p50 = sorted[Math.floor(sorted.length * 0.5)];
  const p95 = sorted[Math.floor(sorted.length * 0.95)];

  const cls = avg < 10 ? 'good' : avg < 30 ? 'warn' : 'bad';
  const el = document.getElementById('pingResult');
  el.innerHTML =
    `收到 <b>${pingResults.length}/${PING_TOTAL}</b> 个回复\\n` +
    `<span class="${cls}">` +
    `  平均: ${avg.toFixed(2)}ms\\n` +
    `  最小: ${min.toFixed(2)}ms  |  最大: ${max.toFixed(2)}ms\\n` +
    `  P50:  ${p50.toFixed(2)}ms  |  P95:  ${p95.toFixed(2)}ms` +
    `</span>\\n\\n` +
    (avg < 10 ? '✓ 延迟极低，完全满足实时 UI 需求' :
     avg < 30 ? '✓ 延迟可接受，UI 体验流畅' :
                '✗ 延迟较高，建议检查网络环境');
}

// ========== 测试2: 状态推送 ==========
async function runStateTest() {
  document.getElementById('btnState').disabled = true;
  document.getElementById('stateResult').textContent = '测试中...';
  stateReceiveTimes = [];
  const ts = performance.now();
  ws.send(JSON.stringify({ action: 'state_test', browser_ts: ts }));
  // 等状态变化全部完成
  await sleep(4000);
  showStateResult();
  document.getElementById('btnState').disabled = false;
}

function showStateResult() {
  const el = document.getElementById('stateResult');
  if (stateReceiveTimes.length === 0) {
    el.textContent = '未收到状态变化';
    return;
  }
  let text = '状态切换延迟:\\n';
  for (const r of stateReceiveTimes) {
    const cls = r.rtt < 10 ? 'good' : r.rtt < 30 ? 'warn' : 'bad';
    text += `  ${r.state.padEnd(10)} → <span class="${cls}">${r.rtt.toFixed(2)}ms</span>\\n`;
  }
  el.innerHTML = text;
}

// ========== 测试3: 压力测试 ==========
async function runBurstTest() {
  document.getElementById('btnBurst').disabled = true;
  document.getElementById('burstResult').textContent = '测试中...';
  burstState = { expected: 100, received: 0, startTime: performance.now(), latencies: [] };
  const ts = performance.now();
  ws.send(JSON.stringify({ action: 'burst_test', count: 100, browser_ts: ts }));
  // 等全部收到
  await sleep(5000);
  if (burstState.received < burstState.expected) showBurstResult();
  document.getElementById('btnBurst').disabled = false;
}

function showBurstResult() {
  const el = document.getElementById('burstResult');
  const s = burstState;
  if (s.received === 0) { el.textContent = '未收到消息'; return; }
  const sorted = [...s.latencies].sort((a, b) => a - b);
  const avg = sorted.reduce((a, b) => a + b, 0) / sorted.length;
  const total = performance.now() - s.startTime;
  const cls = avg < 15 ? 'good' : avg < 50 ? 'warn' : 'bad';
  el.innerHTML =
    `收到 <b>${s.received}/${s.expected}</b> 条消息\\n` +
    `总耗时: ${total.toFixed(0)}ms\\n` +
    `<span class="${cls}">` +
    `  平均延迟: ${avg.toFixed(2)}ms\\n` +
    `  最小: ${sorted[0].toFixed(2)}ms  |  最大: ${sorted[sorted.length-1].toFixed(2)}ms` +
    `</span>`;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
connect();
</script>
</body>
</html>
"""


@app.get("/")
async def index():
    return HTMLResponse(HTML)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            if action == "ping":
                # 立即回复 pong
                await websocket.send_json({"type": "pong", "id": data["id"]})

            elif action == "state_test":
                # 模拟状态切换序列: idle -> listening -> speaking -> listening -> idle
                browser_ts = data["browser_ts"]
                states = ["idle", "listening", "speaking", "listening", "idle"]
                for state in states:
                    await websocket.send_json({
                        "type": "state_change",
                        "state": state,
                        "server_ts": time.time() * 1000,
                        "browser_ts": browser_ts,
                    })
                    await asyncio.sleep(0.6)

            elif action == "burst_test":
                # 连续快速发送 N 条消息
                count = data.get("count", 100)
                browser_ts = data["browser_ts"]
                for i in range(count):
                    await websocket.send_json({
                        "type": "burst",
                        "seq": i,
                        "browser_ts": browser_ts,
                    })
                    # 不加 sleep，全速发送

    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("  Web UI 延迟测试服务器")
    print("  平板浏览器访问: http://<开发板IP>:8080")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8080)
