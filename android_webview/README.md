# AI Agent Android WebView

最小 Android WebView 客户端，全屏 + 横屏加载 `http://192.168.10.1:8080/`。

## 环境
- Ubuntu 24.04 + Android Studio Hedgehog (2023.1.1) 或更新
- compileSdk 34 / minSdk 24 / Kotlin 1.9.22 / AGP 8.2.2 / JDK 17

## 首次打开
1. `sudo snap install android-studio --classic`
2. Android Studio → **Open** → 选 `android_webview/` 这个目录
3. 让它自动下载 Gradle 8.2 和 SDK Platform 34 / Build-Tools 34
4. 等 Gradle Sync 完成（首次会久）

## 构建 APK
```bash
cd android_webview
./gradlew assembleDebug
# 产物：app/build/outputs/apk/debug/app-debug.apk
```

> Gradle Wrapper 文件（`gradlew`/`gradle-wrapper.jar`）由 Android Studio
> 在首次 Sync 时自动生成。如想纯命令行构建，先在已有 Android Studio
> 的环境里执行一次 `gradle wrapper --gradle-version 8.2` 生成。

## 安装到平板
```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```
或者把 APK 拷到平板手动点装（需要在"设置 → 安全 → 未知来源"打开权限）。

## 关键配置点
| 文件 | 作用 |
| --- | --- |
| `MainActivity.kt` | 全屏沉浸式 + WebView JS/DOM/媒体自动播放，长按返回退出 |
| `AndroidManifest.xml` | `usesCleartextTraffic=true`、横屏锁定、`adjustResize` |
| `network_security_config.xml` | 放行 192.168.10.1 的 HTTP 明文 |
| `themes.xml` | NoActionBar + 全屏黑底 + cutout shortEdges |
| `activity_main.xml` | 一个占满屏的 WebView |

## 改服务端 IP
直接编辑 `MainActivity.kt` 里的 `TARGET_URL` 常量；同时把
`network_security_config.xml` 的 `<domain>` 改成新 IP。

## 调试
- USB 连平板，开发者选项 → USB 调试
- Chrome 桌面版打开 `chrome://inspect`，可以看到 WebView 里的页面
- WebView 调试已默认开启（debuggable=true）
