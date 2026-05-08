/* AI Agent Console — Service Worker (minimal)
 *
 * 这个 sw.js 只为满足"PWA 可安装"的最低要求而存在。
 * 它什么都不缓存（应用是局域网实时控制台，离线没意义），
 * fetch 直接透传给网络。注册成功后，浏览器才会把页面识别为
 * 可安装 PWA，从主屏图标启动时进入全屏 standalone / fullscreen 模式。
 */

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // 直接走网络，不做缓存
});
