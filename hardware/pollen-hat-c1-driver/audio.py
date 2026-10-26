#!/usr/bin/env python3
"""Small ALSA helper for the tested Pollen C1 + ZERO 3W setup."""
import argparse
import array
import pathlib
import subprocess
import sys
import tempfile
import time
import wave

CARD = 'PollenHATC1'
DEVICE = 'hw:CARD=PollenHATC1,DEV=0'

def mix(*args):
    subprocess.run(['amixer', '-q', '-c', CARD, 'sset', *args], check=True)

def mute():
    mix('Line', 'mute')
    mix('PCM', '0%')

def initialize():
    if not hasattr(__import__('os'), 'geteuid') or __import__('os').geteuid() != 0:
        raise RuntimeError('init requires sudo')
    for attempt in range(30):
        if CARD in pathlib.Path('/proc/asound/cards').read_text():
            break
        time.sleep(1)
    else:
        raise RuntimeError('PollenHATC1 card not found after 30 seconds')
    state = pathlib.Path(__file__).resolve().with_name('onboard-muted.state')
    subprocess.run(['/usr/sbin/alsactl', '-f', str(state), 'restore', CARD], check=True)
    mute()
    print('Internal microphone configured on LEFT channel. Speaker muted.')

def converted_samples(data, gain, duplicate_left, fade_frames=3840):
    samples = array.array('h')
    samples.frombytes(data)
    if sys.byteorder != 'little':
        samples.byteswap()
    frames = len(samples) // 2
    for frame in range(frames):
        fade = min(1.0, frame / max(1, fade_frames), (frames - 1 - frame) / max(1, fade_frames))
        left = samples[2 * frame]
        right = left if duplicate_left else samples[2 * frame + 1]
        for channel, value in enumerate((left, right)):
            samples[2 * frame + channel] = max(-4915, min(4915, round(value * gain * fade)))
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples.tobytes()

def play(args):
    with wave.open(str(args.file), 'rb') as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (2, 2, 48000, 'NONE'):
            raise ValueError('Requires a 48 kHz 16-bit stereo PCM WAV file')
        # Bound playback and memory use to at most 30 seconds.
        data = source.readframes(min(source.getnframes(), 48000 * 30))
    with tempfile.TemporaryDirectory(prefix='pollen-play-') as temp:
        output = pathlib.Path(temp) / 'play.wav'
        with wave.open(str(output), 'wb') as target:
            target.setnchannels(2)
            target.setsampwidth(2)
            target.setframerate(48000)
            target.writeframes(converted_samples(data, args.gain, args.mic))
        try:
            mute()
            mix('Line DAC', '100%')
            mix('Left Line Mixer DACL1', 'on')
            mix('Right Line Mixer DACR1', 'on')
            mix('PCM', str(args.volume) + '%')
            mix('Line', '0dB', 'unmute')
            subprocess.run(['aplay', '-q', '-D', DEVICE, str(output)], check=True, timeout=35)
        finally:
            mute()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('init')
    sub.add_parser('mute')
    record = sub.add_parser('record')
    record.add_argument('file', type=pathlib.Path)
    play_parser = sub.add_parser('play')
    play_parser.add_argument('file', type=pathlib.Path)
    play_parser.add_argument('--mic', action='store_true', help='copy microphone LEFT channel to both outputs')
    play_parser.add_argument('--gain', type=float, default=1.0)
    play_parser.add_argument('--volume', type=int, default=65)
    args = parser.parse_args()
    if args.action == 'init':
        initialize()
    elif args.action == 'mute':
        mute()
    elif args.action == 'record':
        if args.file.exists():
            raise ValueError('Output already exists; choose another filename')
        if subprocess.run(['systemctl', 'is-active', '--quiet', 'pollen-hat-audio-init.service']).returncode:
            raise RuntimeError('Audio init service is not active; check systemctl status pollen-hat-audio-init.service')
        mute()
        subprocess.run(['arecord', '-D', DEVICE, '-t', 'wav', '-f', 'S16_LE', '-r', '48000', '-c', '2', '-d', '10', str(args.file)], check=True, timeout=15)
        print('Recorded 10 seconds. Stop the phone audio before playback.')
    elif args.action == 'play':
        if not 0 <= args.volume <= 100 or not 0 < args.gain <= 20:
            raise ValueError('volume: 0..100; gain: greater than 0 and at most 20')
        play(args)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
