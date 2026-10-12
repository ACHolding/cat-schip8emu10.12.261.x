# kondo's chip 8 emulator
# Single-file Python 3 + tkinter, mGBA-style front end.
# 600x400 game view, centred on screen, with a status bar underneath.
# Menu strip: File (Load ROM, Exit) / Emulation (Play Game, Pause, Reset,
# Speed) / Video (Palette) / Audio (Mute) / Help (Help, Controls, About).
# FILES = OFF: nothing is ever written to disk, no config/save files.
# "Play Game" with nothing loaded runs the built-in demo ROM; "Load ROM"
# opens any .ch8 file read-only and starts it, like mGBA does.
# Audio engine: Kondo Audio Engine, a streaming synth thread (square / pulse /
#   triangle / sine, envelope + low-pass) feeding sounddevice -> pygame ->
#   Windows waveOut (ctypes, no deps) -> Linux pacat/pw-cat/aplay -> winsound
#   -> silent. All sound is generated in memory.

import sys
import time
import random
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from array import array

TITLE = "kondo's chip 8 emulator"
VIEW_W, VIEW_H = 600, 400
CHIP_W, CHIP_H = 64, 32
SCALE = 9                                   # 64x32 -> 576x288
OFF_X = (VIEW_W - CHIP_W * SCALE) // 2      # 12
STATUS_H = 22                               # status bar height
CANVAS_H = VIEW_H - STATUS_H                # 378: game view + status = 400
OFF_Y = (CANVAS_H - CHIP_H * SCALE) // 2    # 45
FPS = 60
CYCLES_PER_FRAME = 11                       # ~660 instructions/sec

# Emulation > Speed  (label, instructions per 60 Hz frame)
SPEEDS = [("Slow (300 IPS)", 5), ("Normal (660 IPS)", 11),
          ("Fast (1200 IPS)", 20), ("Turbo (3000 IPS)", 50)]

# Video > Palette  (label, pixel off, pixel on)
PALETTES = [
    ("Phosphor Green", "#0b1a10", "#7cff9b"),
    ("Classic B/W", "#000000", "#ffffff"),
    ("Game Boy", "#0f380f", "#9bbc0f"),
    ("Amber", "#1a0f00", "#ffb000"),
    ("Kondo Blue", "#0a1028", "#7fb8ff"),
]

COLOR_BG = "#1e1e1e"        # mGBA-style dark backdrop around the screen
COLOR_STATUS = "#f0f0f0"

FONT = bytes([
    0xF0, 0x90, 0x90, 0x90, 0xF0, 0x20, 0x60, 0x20, 0x20, 0x70,
    0xF0, 0x10, 0xF0, 0x80, 0xF0, 0xF0, 0x10, 0xF0, 0x10, 0xF0,
    0x90, 0x90, 0xF0, 0x10, 0x10, 0xF0, 0x80, 0xF0, 0x10, 0xF0,
    0xF0, 0x80, 0xF0, 0x90, 0xF0, 0xF0, 0x10, 0x20, 0x40, 0x40,
    0xF0, 0x90, 0xF0, 0x90, 0xF0, 0xF0, 0x90, 0xF0, 0x10, 0xF0,
    0xF0, 0x90, 0xF0, 0x90, 0x90, 0xE0, 0x90, 0xE0, 0x90, 0xE0,
    0xF0, 0x80, 0x80, 0x80, 0xF0, 0xE0, 0x90, 0x90, 0x90, 0xE0,
    0xF0, 0x80, 0xF0, 0x80, 0xF0, 0xF0, 0x80, 0xF0, 0x80, 0x80,
])
FONT_ADDR = 0x50

# Built-in demo: a block bouncing around the screen, beeping on each wall hit.
DEMO_ROM = bytes([
    0x60, 0x0A, 0x61, 0x05, 0x62, 0x01, 0x63, 0x01,  # 200 V0=10 V1=5 V2=1 V3=1
    0xA2, 0x30, 0xD0, 0x14,                          # 208 I=sprite, draw
    0x65, 0x03, 0xF5, 0x15,                          # 20C DT=3
    0xF5, 0x07, 0x35, 0x00, 0x12, 0x10,              # 210 wait for DT==0
    0xD0, 0x14,                                      # 216 erase
    0x80, 0x24, 0x81, 0x34,                          # 218 x+=dx, y+=dy
    0x40, 0x3C, 0x22, 0x40,                          # 21C x==60 -> go left
    0x40, 0x00, 0x22, 0x48,                          # 220 x==0  -> go right
    0x41, 0x1C, 0x22, 0x50,                          # 224 y==28 -> go up
    0x41, 0x00, 0x22, 0x58,                          # 228 y==0  -> go down
    0xD0, 0x14, 0x12, 0x0C,                          # 22C draw, loop
    0xF0, 0xF0, 0xF0, 0xF0,                          # 230 4x4 block sprite
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,              # 234 pad
    0x62, 0xFF, 0x64, 0x02, 0xF4, 0x18, 0x00, 0xEE,  # 240 dx=-1, beep, ret
    0x62, 0x01, 0x64, 0x02, 0xF4, 0x18, 0x00, 0xEE,  # 248 dx=+1, beep, ret
    0x63, 0xFF, 0x64, 0x02, 0xF4, 0x18, 0x00, 0xEE,  # 250 dy=-1, beep, ret
    0x63, 0x01, 0x64, 0x02, 0xF4, 0x18, 0x00, 0xEE,  # 258 dy=+1, beep, ret
])

# PC keyboard -> CHIP-8 hex keypad
KEYMAP = {
    "1": 0x1, "2": 0x2, "3": 0x3, "4": 0xC,
    "q": 0x4, "w": 0x5, "e": 0x6, "r": 0xD,
    "a": 0x7, "s": 0x8, "d": 0x9, "f": 0xE,
    "z": 0xA, "x": 0x0, "c": 0xB, "v": 0xF,
}


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------
class Chip8:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mem = bytearray(4096)
        self.mem[FONT_ADDR:FONT_ADDR + len(FONT)] = FONT
        self.v = [0] * 16
        self.i = 0
        self.pc = 0x200
        self.stack = []
        self.dt = 0
        self.st = 0
        self.disp = bytearray(CHIP_W * CHIP_H)
        self.keys = [False] * 16
        self.waiting = False
        self.released = None
        self.draw_flag = True
        self.halted = False
        self.error = None

    def load(self, data):
        self.reset()
        data = bytes(data)[:4096 - 0x200]
        self.mem[0x200:0x200 + len(data)] = data

    def tick_timers(self):
        if self.dt:
            self.dt -= 1
        if self.st:
            self.st -= 1

    def step(self):
        if self.halted:
            return
        mem, v = self.mem, self.v
        op = (mem[self.pc] << 8) | mem[(self.pc + 1) & 0xFFF]
        self.pc = (self.pc + 2) & 0xFFF
        x = (op >> 8) & 0xF
        y = (op >> 4) & 0xF
        n = op & 0xF
        nn = op & 0xFF
        nnn = op & 0xFFF
        top = op >> 12

        if top == 0x0:
            if op == 0x00E0:
                self.disp[:] = bytes(CHIP_W * CHIP_H)
                self.draw_flag = True
            elif op == 0x00EE:
                if not self.stack:
                    return self._fault("stack underflow")
                self.pc = self.stack.pop()
            # 0NNN (machine-code call) is ignored, as on modern interpreters
        elif top == 0x1:
            if nnn == (self.pc - 2) & 0xFFF:
                self.halted = True  # infinite self-jump = program finished
            self.pc = nnn
        elif top == 0x2:
            if len(self.stack) >= 16:
                return self._fault("stack overflow")
            self.stack.append(self.pc)
            self.pc = nnn
        elif top == 0x3:
            if v[x] == nn:
                self.pc = (self.pc + 2) & 0xFFF
        elif top == 0x4:
            if v[x] != nn:
                self.pc = (self.pc + 2) & 0xFFF
        elif top == 0x5:
            if v[x] == v[y]:
                self.pc = (self.pc + 2) & 0xFFF
        elif top == 0x6:
            v[x] = nn
        elif top == 0x7:
            v[x] = (v[x] + nn) & 0xFF
        elif top == 0x8:
            if n == 0x0:
                v[x] = v[y]
            elif n == 0x1:
                v[x] |= v[y]; v[0xF] = 0
            elif n == 0x2:
                v[x] &= v[y]; v[0xF] = 0
            elif n == 0x3:
                v[x] ^= v[y]; v[0xF] = 0
            elif n == 0x4:
                r = v[x] + v[y]
                v[x] = r & 0xFF; v[0xF] = 1 if r > 0xFF else 0
            elif n == 0x5:
                f = 1 if v[x] >= v[y] else 0
                v[x] = (v[x] - v[y]) & 0xFF; v[0xF] = f
            elif n == 0x6:
                f = v[x] & 1
                v[x] >>= 1; v[0xF] = f
            elif n == 0x7:
                f = 1 if v[y] >= v[x] else 0
                v[x] = (v[y] - v[x]) & 0xFF; v[0xF] = f
            elif n == 0xE:
                f = (v[x] >> 7) & 1
                v[x] = (v[x] << 1) & 0xFF; v[0xF] = f
        elif top == 0x9:
            if v[x] != v[y]:
                self.pc = (self.pc + 2) & 0xFFF
        elif top == 0xA:
            self.i = nnn
        elif top == 0xB:
            self.pc = (nnn + v[0]) & 0xFFF
        elif top == 0xC:
            v[x] = random.randint(0, 255) & nn
        elif top == 0xD:
            self._draw(v[x], v[y], n)
        elif top == 0xE:
            if nn == 0x9E:
                if self.keys[v[x] & 0xF]:
                    self.pc = (self.pc + 2) & 0xFFF
            elif nn == 0xA1:
                if not self.keys[v[x] & 0xF]:
                    self.pc = (self.pc + 2) & 0xFFF
        elif top == 0xF:
            if nn == 0x07:
                v[x] = self.dt
            elif nn == 0x0A:
                # Wait for a key press AND release (original behaviour).
                if not self.waiting:
                    self.waiting = True
                    self.released = None
                if self.released is None:
                    self.pc = (self.pc - 2) & 0xFFF
                else:
                    v[x] = self.released
                    self.waiting = False
                    self.released = None
            elif nn == 0x15:
                self.dt = v[x]
            elif nn == 0x18:
                self.st = v[x]
            elif nn == 0x1E:
                self.i = (self.i + v[x]) & 0xFFF
            elif nn == 0x29:
                self.i = FONT_ADDR + (v[x] & 0xF) * 5
            elif nn == 0x33:
                val = v[x]
                mem[self.i & 0xFFF] = val // 100
                mem[(self.i + 1) & 0xFFF] = (val // 10) % 10
                mem[(self.i + 2) & 0xFFF] = val % 10
            elif nn == 0x55:
                for r in range(x + 1):
                    mem[(self.i + r) & 0xFFF] = v[r]
            elif nn == 0x65:
                for r in range(x + 1):
                    v[r] = mem[(self.i + r) & 0xFFF]

    def _draw(self, vx, vy, h):
        x0 = vx % CHIP_W
        y0 = vy % CHIP_H
        disp, mem = self.disp, self.mem
        self.v[0xF] = 0
        for row in range(h):
            py = y0 + row
            if py >= CHIP_H:
                break
            bits = mem[(self.i + row) & 0xFFF]
            base = py * CHIP_W
            for col in range(8):
                px = x0 + col
                if px >= CHIP_W:
                    break
                if bits & (0x80 >> col):
                    idx = base + px
                    if disp[idx]:
                        self.v[0xF] = 1
                    disp[idx] ^= 1
        self.draw_flag = True

    def _fault(self, msg):
        self.halted = True
        self.error = f"{msg} at {self.pc - 2:03X}"


# ---------------------------------------------------------------------------
# KONDO AUDIO ENGINE
# ---------------------------------------------------------------------------
# A small streaming synth. A background thread renders 16-bit mono PCM in
# blocks and pushes it to the first output that works on this machine:
#
#   1. sounddevice   (pip install sounddevice)      - any OS
#   2. pygame mixer  (pip install pygame-ce)        - any OS
#   3. waveOut       (winmm via ctypes, built in)   - Windows, no deps
#   4. pacat / pw-cat / aplay pipe                  - Linux, no deps
#   5. winsound.Beep                                - Windows last resort
#   6. silent
#
# Everything is generated in memory: no audio files are read or written.
# The CHIP-8 only has an on/off buzzer, so the synth is a gated oscillator
# with a short attack/release envelope (no clicks) and a gentle low-pass.

AUDIO_RATE = 44100
AUDIO_BLOCK = 512                      # samples per block (~11.6 ms)

WAVEFORMS = ["Square", "Pulse 25%", "Triangle", "Sine"]
TONES = [("Low (220 Hz)", 220.0), ("Classic (440 Hz)", 440.0),
         ("High (880 Hz)", 880.0)]
VOLUMES = [("25%", 0.25), ("50%", 0.50), ("75%", 0.75), ("100%", 1.0)]


class _Backend:
    name = "silent"
    paced = False          # True = engine must pace itself (non-blocking sink)

    def write(self, pcm):
        pass

    def close(self):
        pass


class _SoundDeviceBackend(_Backend):
    name = "sounddevice"

    def __init__(self):
        import sounddevice as sd
        self.stream = sd.RawOutputStream(samplerate=AUDIO_RATE, channels=1,
                                         dtype="int16", blocksize=AUDIO_BLOCK,
                                         latency="low")
        self.stream.start()

    def write(self, pcm):
        self.stream.write(pcm)            # blocks until there is room

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


class _PygameBackend(_Backend):
    name = "pygame"

    def __init__(self):
        import pygame
        self.pg = pygame
        if not pygame.mixer.get_init():
            pygame.mixer.pre_init(AUDIO_RATE, -16, 1, AUDIO_BLOCK)
            pygame.mixer.init()
        rate, size, ch = pygame.mixer.get_init()
        if rate != AUDIO_RATE or size != -16:
            raise RuntimeError("mixer opened with an unexpected format")
        self.channels = ch
        self.chan = pygame.mixer.Channel(0)

    def write(self, pcm):
        if self.channels == 2:            # mixer forced stereo: duplicate
            mono = array("h")
            mono.frombytes(pcm)
            st = array("h", [0]) * (len(mono) * 2)
            st[0::2] = mono
            st[1::2] = mono
            pcm = st.tobytes()
        snd = self.pg.mixer.Sound(buffer=pcm)
        while self.chan.get_queue() is not None:
            time.sleep(0.002)
        if self.chan.get_busy():
            self.chan.queue(snd)
        else:
            self.chan.play(snd)

    def close(self):
        try:
            self.chan.stop()
        except Exception:
            pass


class _WaveOutBackend(_Backend):
    """Windows winmm waveOut, double/quad buffered, pure ctypes."""
    name = "waveOut"
    NBUF = 4

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        self.winmm = ctypes.WinDLL("winmm")

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                        ("nSamplesPerSec", wintypes.DWORD),
                        ("nAvgBytesPerSec", wintypes.DWORD),
                        ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                        ("cbSize", wintypes.WORD)]

        class WAVEHDR(ctypes.Structure):
            pass
        WAVEHDR._fields_ = [("lpData", ctypes.c_void_p),
                            ("dwBufferLength", wintypes.DWORD),
                            ("dwBytesRecorded", wintypes.DWORD),
                            ("dwUser", ctypes.c_size_t),
                            ("dwFlags", wintypes.DWORD),
                            ("dwLoops", wintypes.DWORD),
                            ("lpNext", ctypes.c_void_p),
                            ("reserved", ctypes.c_size_t)]
        self.WAVEHDR = WAVEHDR

        fmt = WAVEFORMATEX(1, 1, AUDIO_RATE, AUDIO_RATE * 2, 2, 16, 0)
        self.handle = ctypes.c_void_p()
        WAVE_MAPPER = 0xFFFFFFFF
        rc = self.winmm.waveOutOpen(ctypes.byref(self.handle), WAVE_MAPPER,
                                    ctypes.byref(fmt), None, None, 0)
        if rc != 0:
            raise RuntimeError(f"waveOutOpen failed ({rc})")

        nbytes = AUDIO_BLOCK * 2
        self.bufs = [ctypes.create_string_buffer(nbytes) for _ in range(self.NBUF)]
        self.hdrs = []
        for b in self.bufs:
            h = WAVEHDR()
            h.lpData = ctypes.cast(b, ctypes.c_void_p)
            h.dwBufferLength = nbytes
            self.winmm.waveOutPrepareHeader(self.handle, ctypes.byref(h),
                                            ctypes.sizeof(WAVEHDR))
            self.hdrs.append(h)
        self.used = [False] * self.NBUF
        self.idx = 0

    def write(self, pcm):
        WHDR_DONE = 0x1
        i = self.idx
        h = self.hdrs[i]
        if self.used[i]:
            deadline = time.perf_counter() + 0.5
            while not (h.dwFlags & WHDR_DONE):
                if time.perf_counter() > deadline:
                    return                # device stalled: drop block
                time.sleep(0.001)
        self.ct.memmove(self.bufs[i], pcm, len(pcm))
        h.dwBufferLength = len(pcm)
        self.winmm.waveOutWrite(self.handle, self.ct.byref(h),
                                self.ct.sizeof(self.WAVEHDR))
        self.used[i] = True
        self.idx = (i + 1) % self.NBUF

    def close(self):
        try:
            self.winmm.waveOutReset(self.handle)
            for h in self.hdrs:
                self.winmm.waveOutUnprepareHeader(self.handle, self.ct.byref(h),
                                                  self.ct.sizeof(self.WAVEHDR))
            self.winmm.waveOutClose(self.handle)
        except Exception:
            pass


class _PipeBackend(_Backend):
    """Linux: stream raw PCM into pacat / pw-cat / aplay over a pipe."""
    paced = True

    COMMANDS = [
        ("pacat", ["pacat", "--raw", "--format=s16le", f"--rate={AUDIO_RATE}",
                   "--channels=1", "--latency-msec=40"]),
        ("pw-cat", ["pw-cat", "--playback", "--format=s16", f"--rate={AUDIO_RATE}",
                    "--channels=1", "--latency=40ms", "-"]),
        ("aplay", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(AUDIO_RATE),
                   "-c", "1", "--buffer-time=60000"]),
    ]

    def __init__(self):
        import shutil
        import subprocess
        for name, cmd in self.COMMANDS:
            if shutil.which(cmd[0]):
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                             stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
                time.sleep(0.05)
                if self.proc.poll() is None:
                    self.name = name
                    return
        raise RuntimeError("no pcm player found")

    def write(self, pcm):
        self.proc.stdin.write(pcm)
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


class AudioEngine:
    """Streaming buzzer synth for the CHIP-8 sound timer."""

    def __init__(self):
        self.muted = False
        self.volume = 0.5
        self.freq = 440.0
        self.wave = "Square"
        self.on = False                  # gate requested by the emulator
        self._blip_until = 0.0           # test-tone gate
        self._phase = 0.0
        self._env = 0.0
        self._lp = 0.0
        self._run = True
        self._lock = threading.Lock()
        self._silence = bytes(AUDIO_BLOCK * 2)
        self.underruns = 0

        self._out = self._open_backend()
        self.backend = self._out.name
        self._beep_mode = self.backend == "winsound"
        self._thread = threading.Thread(target=self._worker, name="kondo-audio",
                                        daemon=True)
        self._thread.start()

    # -- backend selection --------------------------------------------------
    def _open_backend(self):
        candidates = [_SoundDeviceBackend, _PygameBackend]
        if sys.platform.startswith("win"):
            candidates.append(_WaveOutBackend)
        elif sys.platform.startswith("linux"):
            candidates.append(_PipeBackend)
        for cls in candidates:
            try:
                return cls()
            except Exception:
                continue
        if sys.platform.startswith("win"):
            try:
                import winsound
                b = _Backend()
                b.name = "winsound"
                b.winsound = winsound
                return b
            except Exception:
                pass
        return _Backend()

    # -- public API (called from the GUI thread) ----------------------------
    def set(self, active):
        self.on = bool(active)

    def blip(self, seconds=0.35):
        """Play a short test tone, even with no game running."""
        self._blip_until = time.perf_counter() + seconds

    def shutdown(self):
        self._run = False
        self.on = False
        self._thread.join(timeout=0.5)
        self._out.close()

    # -- synthesis ----------------------------------------------------------
    def _gate(self):
        return (self.on or time.perf_counter() < self._blip_until) and not self.muted

    def _render(self, gate):
        """Render one block of int16 PCM. Returns bytes."""
        env = self._env
        if not gate and env <= 0.0:
            self._lp = 0.0
            return self._silence

        rate = AUDIO_RATE
        step = self.freq / rate
        amp = 32767.0 * 0.6 * self.volume
        attack = 1.0 / (0.002 * rate)    # 2 ms
        release = 1.0 / (0.010 * rate)   # 10 ms
        wave = self.wave
        phase = self._phase
        lp = self._lp
        a = 0.35                         # one-pole low-pass (softens the edge)
        out = array("h", self._silence)
        for n in range(AUDIO_BLOCK):
            if gate:
                env = env + attack if env < 1.0 else 1.0
            else:
                env = env - release if env > 0.0 else 0.0
            phase += step
            if phase >= 1.0:
                phase -= 1.0
            if wave == "Square":
                s = 1.0 if phase < 0.5 else -1.0
            elif wave == "Pulse 25%":
                s = 1.0 if phase < 0.25 else -1.0
            elif wave == "Triangle":
                s = 4.0 * phase - 1.0 if phase < 0.5 else 3.0 - 4.0 * phase
            else:
                s = _SINE[int(phase * 1024) & 1023]
            lp += a * (s * env - lp)
            out[n] = int(lp * amp)
        self._phase = phase
        self._env = env if env > 0.0 else 0.0
        self._lp = lp
        return out.tobytes()

    # -- worker thread ------------------------------------------------------
    def _worker(self):
        if self._beep_mode:
            self._beep_worker()
            return
        out = self._out
        paced = out.paced
        block_s = AUDIO_BLOCK / AUDIO_RATE
        ahead = 0.045                    # how far the paced sink may run ahead
        t0 = time.perf_counter()
        sent = 0.0
        while self._run:
            pcm = self._render(self._gate())
            try:
                out.write(pcm)
            except Exception:
                self.backend = "silent (output lost)"
                self._out = _Backend()
                out = self._out
                paced = True
                t0 = time.perf_counter()
                sent = 0.0
            if paced:
                sent += block_s
                now = time.perf_counter()
                lead = sent - (now - t0)
                if lead > ahead:
                    time.sleep(lead - ahead)
                elif lead < -0.25:       # fell way behind (e.g. sleep): resync
                    self.underruns += 1
                    t0 = now
                    sent = 0.0

    def _beep_worker(self):
        ws = self._out.winsound
        while self._run:
            if self._gate():
                try:
                    ws.Beep(int(self.freq), 40)
                except Exception:
                    time.sleep(0.04)
            else:
                time.sleep(0.005)


_SINE = [__import__("math").sin(2.0 * 3.141592653589793 * i / 1024) for i in range(1024)]


# ---------------------------------------------------------------------------
# APP  (mGBA-style front end)
# ---------------------------------------------------------------------------
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.resizable(False, False)
        self.root.configure(bg=COLOR_BG)

        self.chip = Chip8()
        self.audio = AudioEngine()
        self.rom_name = None          # None = no game loaded yet
        self.rom_data = None
        self.running = False          # a game has been started
        self.paused = False

        self.v_pause = tk.BooleanVar(value=False)
        self.v_mute = tk.BooleanVar(value=False)
        self.v_speed = tk.IntVar(value=CYCLES_PER_FRAME)
        self.v_palette = tk.IntVar(value=0)
        self.v_volume = tk.DoubleVar(value=self.audio.volume)
        self.v_tone = tk.DoubleVar(value=self.audio.freq)
        self.v_wave = tk.StringVar(value=self.audio.wave)

        self._build_menu()
        self._build_statusbar()       # packed first so it always keeps its 22px
        self._build_view()
        self._center()
        self._show_idle()

        self.root.bind("<KeyPress>", self._key_down)
        self.root.bind("<KeyRelease>", self._key_up)
        self.root.bind("<Control-o>", lambda e: self.load_rom())
        self.root.bind("<Control-g>", lambda e: self.play_game())
        self.root.bind("<Control-p>", lambda e: self.toggle_pause())
        self.root.bind("<Control-r>", lambda e: self.reset_game())
        self.root.bind("<Control-m>", lambda e: self.toggle_mute())
        self.root.bind("<Control-t>", lambda e: self.audio.blip())
        self.root.bind("<Control-q>", lambda e: self.exit())
        self.root.bind("<F1>", lambda e: self.show_controls())
        self.root.protocol("WM_DELETE_WINDOW", self.exit)

        self._last = time.perf_counter()
        self._acc = 0.0
        self._fps_t = self._last
        self._fps_n = 0
        self._fps = 0
        self.root.after(1, self._loop)

    # -- layout --------------------------------------------------------------
    def _build_menu(self):
        bar = tk.Menu(self.root)

        m_file = tk.Menu(bar, tearoff=0)
        m_file.add_command(label="Load ROM...", accelerator="Ctrl+O", command=self.load_rom)
        m_file.add_separator()
        m_file.add_command(label="Exit", accelerator="Ctrl+Q", command=self.exit)
        bar.add_cascade(label="File", menu=m_file)

        m_emu = tk.Menu(bar, tearoff=0)
        m_emu.add_command(label="Play Game", accelerator="Ctrl+G", command=self.play_game)
        m_emu.add_checkbutton(label="Pause", accelerator="Ctrl+P",
                              variable=self.v_pause, command=self._pause_from_menu)
        m_emu.add_command(label="Reset", accelerator="Ctrl+R", command=self.reset_game)
        m_emu.add_command(label="Stop", command=self.stop_game)
        m_emu.add_separator()
        m_speed = tk.Menu(m_emu, tearoff=0)
        for label, cyc in SPEEDS:
            m_speed.add_radiobutton(label=label, value=cyc, variable=self.v_speed)
        m_emu.add_cascade(label="Speed", menu=m_speed)
        bar.add_cascade(label="Emulation", menu=m_emu)

        m_video = tk.Menu(bar, tearoff=0)
        m_pal = tk.Menu(m_video, tearoff=0)
        for i, (label, _off, _on) in enumerate(PALETTES):
            m_pal.add_radiobutton(label=label, value=i, variable=self.v_palette,
                                  command=self._palette_changed)
        m_video.add_cascade(label="Palette", menu=m_pal)
        bar.add_cascade(label="Video", menu=m_video)

        m_audio = tk.Menu(bar, tearoff=0)
        m_audio.add_checkbutton(label="Mute", accelerator="Ctrl+M",
                                variable=self.v_mute, command=self._mute_from_menu)
        m_audio.add_command(label="Test Tone", accelerator="Ctrl+T",
                            command=self.audio.blip)
        m_audio.add_separator()
        m_vol = tk.Menu(m_audio, tearoff=0)
        for label, vol in VOLUMES:
            m_vol.add_radiobutton(label=label, value=vol, variable=self.v_volume,
                                  command=self._audio_changed)
        m_audio.add_cascade(label="Volume", menu=m_vol)
        m_tone = tk.Menu(m_audio, tearoff=0)
        for label, hz in TONES:
            m_tone.add_radiobutton(label=label, value=hz, variable=self.v_tone,
                                   command=self._audio_changed)
        m_audio.add_cascade(label="Tone", menu=m_tone)
        m_wave = tk.Menu(m_audio, tearoff=0)
        for name in WAVEFORMS:
            m_wave.add_radiobutton(label=name, value=name, variable=self.v_wave,
                                   command=self._audio_changed)
        m_audio.add_cascade(label="Waveform", menu=m_wave)
        m_audio.add_separator()
        m_audio.add_command(label=f"Engine: {self.audio.backend}", state="disabled")
        bar.add_cascade(label="Audio", menu=m_audio)

        m_help = tk.Menu(bar, tearoff=0)
        m_help.add_command(label="Help...", command=self.show_help)
        m_help.add_command(label="Controls...", accelerator="F1", command=self.show_controls)
        m_help.add_separator()
        m_help.add_command(label="About...", command=self.show_about)
        bar.add_cascade(label="Help", menu=m_help)

        self.root.config(menu=bar)

    def _build_view(self):
        self.canvas = tk.Canvas(self.root, width=VIEW_W, height=CANVAS_H,
                                bg=COLOR_BG, highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.small = tk.PhotoImage(width=CHIP_W, height=CHIP_H)
        self.big = self.small.zoom(SCALE)
        self.img_item = self.canvas.create_image(OFF_X, OFF_Y, anchor="nw", image=self.big)

        # Idle screen (shown until a game is started), like mGBA's empty window.
        cx, cy = VIEW_W // 2, CANVAS_H // 2
        self.idle_items = [
            self.canvas.create_text(cx, cy - 46, text="kondo's chip 8",
                                    fill="#e8e8e8", font=("Helvetica", 30, "bold")),
            self.canvas.create_text(cx, cy - 10, text="CHIP-8 EMULATOR",
                                    fill="#8a8a8a", font=("Helvetica", 11, "bold")),
            self.canvas.create_text(cx, cy + 36,
                                    text="File > Load ROM...   to open a .ch8 game",
                                    fill="#b0b0b0", font=("Helvetica", 10)),
            self.canvas.create_text(cx, cy + 58,
                                    text="Emulation > Play Game   to run the built-in demo",
                                    fill="#b0b0b0", font=("Helvetica", 10)),
        ]
        self.pause_item = self.canvas.create_text(
            cx, OFF_Y + CHIP_H * SCALE + 22, text="", fill="#ffd34d",
            font=("Helvetica", 11, "bold"))

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=COLOR_STATUS, bd=1, relief="sunken",
                       height=STATUS_H)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)     # lock the bar at exactly STATUS_H
        self.status_left = tk.Label(bar, text="", bg=COLOR_STATUS, anchor="w",
                                    font=("Helvetica", 9))
        self.status_left.pack(side="left", padx=6)
        self.status_right = tk.Label(bar, text="", bg=COLOR_STATUS, anchor="e",
                                     font=("Helvetica", 9))
        self.status_right.pack(side="right", padx=6)

    def _center(self):
        # Window is exactly 600x400 (game view + status bar), centred on screen.
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - VIEW_W) // 2
        y = (sh - VIEW_H) // 2
        self.root.geometry(f"{VIEW_W}x{VIEW_H}+{x}+{y}")
        self.root.minsize(VIEW_W, VIEW_H)
        self.root.maxsize(VIEW_W, VIEW_H)

    # -- screen states -------------------------------------------------------
    def _show_idle(self):
        self.canvas.itemconfig(self.img_item, state="hidden")
        for it in self.idle_items:
            self.canvas.itemconfig(it, state="normal")
        self.canvas.itemconfig(self.pause_item, text="")
        self.root.title(TITLE)
        self.status_left.config(text="No game loaded")
        self.status_right.config(text=f"Audio: {self.audio.backend}")

    def _show_game(self):
        for it in self.idle_items:
            self.canvas.itemconfig(it, state="hidden")
        self.canvas.itemconfig(self.img_item, state="normal")
        self.chip.draw_flag = True

    def _start(self, data, name):
        self.rom_data = data
        self.rom_name = name
        self.chip.load(data)
        self.running = True
        self._set_paused(False)
        self._show_game()
        self._update_status()

    # -- menu actions --------------------------------------------------------
    def load_rom(self):
        self.audio.set(False)
        path = filedialog.askopenfilename(
            parent=self.root, title="Load ROM",
            filetypes=[("CHIP-8 ROMs", "*.ch8 *.c8 *.rom"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            messagebox.showerror(TITLE, f"Could not read ROM:\n{e}", parent=self.root)
            return
        if not data:
            messagebox.showerror(TITLE, "That ROM is empty.", parent=self.root)
            return
        if len(data) > 4096 - 0x200:
            messagebox.showwarning(TITLE, "ROM is larger than 3584 bytes; it was truncated.",
                                   parent=self.root)
        self._start(data, path.replace("\\", "/").rsplit("/", 1)[-1])

    def play_game(self):
        if self.running:
            self._set_paused(False)            # already running: just resume
        elif self.rom_data is not None:
            self._start(self.rom_data, self.rom_name)
        else:
            self._start(DEMO_ROM, "built-in demo")

    def reset_game(self):
        if self.rom_data is not None:
            self._start(self.rom_data, self.rom_name)

    def stop_game(self):
        self.running = False
        self._set_paused(False)
        self.audio.set(False)
        self._show_idle()

    def toggle_pause(self):
        if self.running:
            self._set_paused(not self.paused)

    def _pause_from_menu(self):
        if self.running:
            self._set_paused(self.v_pause.get())
        else:
            self.v_pause.set(False)

    def _set_paused(self, value):
        self.paused = value
        self.v_pause.set(value)
        self.canvas.itemconfig(self.pause_item, text="PAUSED" if value else "")
        if value:
            self.audio.set(False)
        self._update_status()

    def toggle_mute(self):
        self.v_mute.set(not self.v_mute.get())
        self._mute_from_menu()

    def _mute_from_menu(self):
        self.audio.muted = self.v_mute.get()
        if self.audio.muted:
            self.audio.set(False)
        self._update_status()

    def _audio_changed(self):
        self.audio.volume = float(self.v_volume.get())
        self.audio.freq = float(self.v_tone.get())
        self.audio.wave = self.v_wave.get()
        self.audio.blip(0.25)          # let the player hear the change
        self._update_status()

    def _palette_changed(self):
        self.chip.draw_flag = True

    def show_help(self):
        messagebox.showinfo("Help", (
            "Getting started\n\n"
            "1. File > Load ROM... and pick a .ch8 file.\n"
            "   The game starts right away.\n"
            "2. Or Emulation > Play Game to run the built-in\n"
            "   demo (a bouncing block that beeps).\n\n"
            "Emulation > Pause / Reset / Stop control the game.\n"
            "Emulation > Speed changes instructions per second;\n"
            "some games want Fast, older ones want Slow.\n"
            "Video > Palette changes the screen colours.\n"
            "Audio > Volume / Tone / Waveform shape the beeper;\n"
            "Audio > Test Tone plays it without a game.\n\n"
            "Files are OFF: nothing is ever saved to disk."),
            parent=self.root)

    def show_controls(self):
        messagebox.showinfo("Controls", (
            "CHIP-8 keypad    Keyboard\n\n"
            "  1 2 3 C         1 2 3 4\n"
            "  4 5 6 D         Q W E R\n"
            "  7 8 9 E         A S D F\n"
            "  A 0 B F         Z X C V\n\n"
            "Ctrl+O  Load ROM       Ctrl+G  Play Game\n"
            "Ctrl+P  Pause          Ctrl+R  Reset\n"
            "Ctrl+M  Mute           Ctrl+T  Test Tone\n"
            "F1      Controls\n"
            "Ctrl+Q  Exit"),
            parent=self.root)

    def show_about(self):
        messagebox.showinfo("About", (
            f"{TITLE}\n\n"
            "Single-file CHIP-8 interpreter\n"
            "Python + tkinter, mGBA-style front end\n"
            "60 Hz timers, adjustable speed\n"
            f"Kondo Audio Engine: {self.audio.backend}\n"
            f"{AUDIO_RATE} Hz 16-bit mono, streamed\n\n"
            "Files: OFF (no config, no saves)"),
            parent=self.root)

    def exit(self):
        self.audio.shutdown()
        self.root.destroy()

    # -- input ---------------------------------------------------------------
    def _key_down(self, e):
        if e.state & 0x4:          # Ctrl held: leave it to the menu shortcuts
            return
        k = e.keysym.lower()
        if k in KEYMAP:
            self.chip.keys[KEYMAP[k]] = True

    def _key_up(self, e):
        k = e.keysym.lower()
        if k in KEYMAP:
            key = KEYMAP[k]
            self.chip.keys[key] = False
            if self.chip.waiting:
                self.chip.released = key

    # -- main loop -----------------------------------------------------------
    def _loop(self):
        now = time.perf_counter()
        self._acc += now - self._last
        self._last = now
        frame = 1.0 / FPS
        steps = 0
        active = self.running and not self.paused
        while self._acc >= frame and steps < 4:   # catch up, but never spiral
            self._acc -= frame
            steps += 1
            if active:
                c = self.chip
                for _ in range(self.v_speed.get()):
                    c.step()
                c.tick_timers()
                self._fps_n += 1
        if self._acc > frame * 4:
            self._acc = 0.0

        self.audio.set(active and self.chip.st > 0)
        if self.running and self.chip.draw_flag:
            self._render()
            self.chip.draw_flag = False

        if now - self._fps_t >= 1.0:
            self._fps = self._fps_n
            self._fps_n = 0
            self._fps_t = now
            if self.running:
                self._update_status()
        self.root.after(4, self._loop)

    def _render(self):
        _label, off, on = PALETTES[self.v_palette.get()]
        d = self.chip.disp
        rows = []
        for y in range(CHIP_H):
            base = y * CHIP_W
            rows.append("{" + " ".join(on if d[base + x] else off
                                       for x in range(CHIP_W)) + "}")
        self.small.put(" ".join(rows))
        self.big = self.small.zoom(SCALE)
        self.canvas.itemconfig(self.img_item, image=self.big)

    def _update_status(self):
        if not self.running:
            return
        if self.chip.error:
            state = "Error: " + self.chip.error
        elif self.paused:
            state = "Paused"
        elif self.chip.halted:
            state = "Halted"
        else:
            state = f"{self._fps} fps"
        self.root.title(f"{TITLE} - {self.rom_name} ({state})")
        self.status_left.config(text=f"{self.rom_name}  |  {state}")
        audio = "muted" if self.audio.muted else self.audio.backend
        speed = next((lbl for lbl, c in SPEEDS if c == self.v_speed.get()), "")
        self.status_right.config(text=f"{speed}  |  Audio: {audio}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
