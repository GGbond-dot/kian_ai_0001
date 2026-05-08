package com.kian.aiagent

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Base64
import android.util.Log
import android.webkit.WebView
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * 平板麦克风桥：原生 AudioRecord 抓 16kHz/16bit/单声道 PCM，
 * 通过 evaluateJavascript 回调到 window.AudioBridgeJS.onPcmChunk。
 *
 * Web 侧调用入口：
 *   AudioBridge.start()  —— 开录
 *   AudioBridge.stop()   —— 停录
 *   AudioBridge.isRecording() —— 状态查询
 */
class AudioBridge(private val webView: WebView) {

    private val recording = AtomicBoolean(false)
    private var recordThread: Thread? = null

    @android.webkit.JavascriptInterface
    fun start(): Boolean {
        if (recording.get()) return true
        recording.set(true)
        recordThread = thread(name = "AudioBridge-rec", start = true) { runLoop() }
        return true
    }

    @android.webkit.JavascriptInterface
    fun stop() {
        recording.set(false)
    }

    @android.webkit.JavascriptInterface
    fun isRecording(): Boolean = recording.get()

    @android.webkit.JavascriptInterface
    fun sampleRate(): Int = SAMPLE_RATE

    @SuppressLint("MissingPermission")
    private fun runLoop() {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        val bufBytes = maxOf(minBuf, FRAME_BYTES * 4)

        val recorder = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufBytes
            )
        } catch (e: Throwable) {
            Log.e(TAG, "AudioRecord 构造失败", e)
            recording.set(false)
            postEvent("error", "audio_record_init_failed")
            return
        }

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "AudioRecord 未初始化, state=${recorder.state}")
            recorder.release()
            recording.set(false)
            postEvent("error", "audio_record_uninitialized")
            return
        }

        try {
            recorder.startRecording()
        } catch (e: Throwable) {
            Log.e(TAG, "startRecording 失败", e)
            recorder.release()
            recording.set(false)
            postEvent("error", "audio_record_start_failed")
            return
        }

        postEvent("started", null)

        val frame = ByteArray(FRAME_BYTES)
        while (recording.get()) {
            val read = recorder.read(frame, 0, frame.size)
            if (read <= 0) continue
            val capturedAt = System.currentTimeMillis()
            val payload = if (read == frame.size) frame else frame.copyOf(read)
            val b64 = Base64.encodeToString(payload, Base64.NO_WRAP)
            // 单引号包字符串，安全因为 base64 不含单引号
            val js = "window.AudioBridgeJS && window.AudioBridgeJS.onPcmChunk('$b64', $capturedAt);"
            webView.post { webView.evaluateJavascript(js, null) }
        }

        try {
            recorder.stop()
        } catch (_: Throwable) {
        }
        recorder.release()
        postEvent("stopped", null)
    }

    private fun postEvent(name: String, detail: String?) {
        val safeDetail = detail?.replace("'", "\\'") ?: ""
        val js = "window.AudioBridgeJS && window.AudioBridgeJS.onEvent && " +
            "window.AudioBridgeJS.onEvent('$name', '$safeDetail');"
        webView.post { webView.evaluateJavascript(js, null) }
    }

    companion object {
        private const val TAG = "AudioBridge"
        private const val SAMPLE_RATE = 16000

        // 20ms / 帧：16000 * 0.02 * 2 bytes = 640 字节
        private const val FRAME_BYTES = 640
    }
}
