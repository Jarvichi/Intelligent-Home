"""ih_speaker voice clips: render TTS audio through Home Assistant (no speaker involved), turn it into ogg/opus with ffmpeg and work out the
duration and the waveform that Discord shows in a voice message. Everything that starts a process or touches the network is injectable."""
import base64
import subprocess
import urllib.request

FFMPEG = 'ffmpeg'
RATE_PCM = 8000          # mono 16-bit samples used to measure duration and waveform
WAVE_BYTES = 256         # Discord waveform: up to 256 amplitude samples, 0..255
MAX_AUDIO = 8 * 1024 * 1024
MAX_SECONDS = 120


class VoiceError(RuntimeError):
    pass


def _run(cmd, data, run=None, timeout=60):
    try:
        r = (run or subprocess.run)(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        raise VoiceError('ffmpeg is not installed')
    except subprocess.TimeoutExpired:
        raise VoiceError('ffmpeg timed out')
    if r.returncode != 0 or not r.stdout:
        raise VoiceError('ffmpeg failed')
    return r.stdout


def to_ogg_opus(audio, run=None):
    """Any audio ffmpeg can read -> ogg/opus bytes (libopus, mono, 48 kHz)."""
    return _run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0', '-vn', '-ac', '1', '-ar', '48000',
                 '-c:a', 'libopus', '-b:a', '32k', '-f', 'ogg', 'pipe:1'], audio, run)


def pcm_samples(audio, run=None):
    """Any audio -> list of absolute amplitudes (0..32768) at RATE_PCM, mono."""
    raw = _run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0', '-vn', '-ac', '1', '-ar', str(RATE_PCM),
                '-f', 's16le', 'pipe:1'], audio, run)
    n = len(raw) // 2
    return [abs(int.from_bytes(raw[i * 2:i * 2 + 2], 'little', signed=True)) for i in range(n)]


def waveform(samples, points=WAVE_BYTES):
    """Absolute amplitudes -> base64 of up to `points` peak values scaled 0..255 (the loudest point is 255)."""
    if not samples:
        return base64.b64encode(b'\x00').decode()
    k = min(points, len(samples))
    peaks = []
    for i in range(k):
        a, b = i * len(samples) // k, max(i * len(samples) // k + 1, (i + 1) * len(samples) // k)
        peaks.append(max(samples[a:b]))
    top = max(peaks) or 1
    return base64.b64encode(bytes(min(255, round(p * 255 / top)) for p in peaks)).decode()


def encode(audio, run=None):
    """Audio bytes -> (ogg_bytes, duration_seconds, waveform_base64). Raises VoiceError."""
    if not audio:
        raise VoiceError('no audio')
    if len(audio) > MAX_AUDIO:
        raise VoiceError('audio too large')
    samples = pcm_samples(audio, run)
    duration = len(samples) / float(RATE_PCM)
    if duration > MAX_SECONDS:
        raise VoiceError('clip longer than %d s' % MAX_SECONDS)
    return to_ogg_opus(audio, run), round(duration, 2), waveform(samples)


def render(ha, engine, text, voice='', opener=None):
    """Ask Home Assistant for the TTS audio of `text` (nothing is played anywhere) and download it. -> bytes. Raises VoiceError."""
    body = {'engine_id': engine, 'message': text}
    if engine == 'tts.piper' and voice:
        body['options'] = {'voice': voice}
    try:
        got = ha.post('tts_get_url', body) or {}
        path = str(got.get('path') or '')
        if not path.startswith('/api/'):
            raise VoiceError('no audio url')
        req = urllib.request.Request(ha.rest_base.split('/api/')[0] + path, headers={'Authorization': 'Bearer ' + ha.token})
        with (opener or urllib.request.urlopen)(req, timeout=60) as r:
            data = r.read(MAX_AUDIO + 1)
    except VoiceError:
        raise
    except Exception as e:
        raise VoiceError('could not get the audio from Home Assistant (%s)' % type(e).__name__)
    if not data:
        raise VoiceError('empty audio')
    return data
