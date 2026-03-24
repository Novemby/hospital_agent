import io
from pydub import AudioSegment

def convert_to_pcm(audio_bytes: bytes) -> bytes:
    """将任意音频格式转换为 PCM 16k 16bit mono"""
    audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
    audio_segment = audio_segment.set_channels(1).set_frame_rate(16000).set_sample_width(2)
    return audio_segment.raw_data