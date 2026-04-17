import pulsectl
import soundcard as sc
import numpy as np

print("--- 步骤1：检查音频设备列表 ---")
try:
    with pulsectl.Pulse('voice-assistant-client') as pulse:
        print("\n[输出设备 (扬声器)]:")
        for sink in pulse.sink_list():
            print(f"  - {sink.name} ({sink.description})")

        print("\n[输入设备 (麦克风)]:")
        for source in pulse.source_list():
            print(f"  - {source.name} ({source.description})")
except Exception as e:
    print(f"获取设备列表失败，请检查 PulseAudio 连接: {e}")

print("\n--- 步骤2：测试录音与回放 ---")
try:
    # 获取默认麦克风和扬声器
    default_mic = sc.default_microphone()
    default_speaker = sc.default_speaker()
    
    print(f"\n使用的麦克风: {default_mic.name}")
    print(f"使用的扬声器: {default_speaker.name}")

    fs = 44100
    rec_sec = 5

    print("\n🎤 开始录音，请对着麦克风说话... (持续 5 秒)")
    # 录制音频
    data = default_mic.record(samplerate=fs, numframes=fs*rec_sec)
    print("✅ 录音结束，正在回放...")
    
    # 播放音频 (加入防爆音处理)
    max_val = np.max(np.abs(data))
    if max_val > 0:
        default_speaker.play(data / max_val, samplerate=fs)
    else:
        print("⚠️ 录制到的是完全的静音，请检查麦克风是否工作。")
        
    print("✅ 测试完毕！")

except Exception as e:
    print(f"录音或播放发生错误: {e}")
