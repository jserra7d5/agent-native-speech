# Discord.py + discord-ext-voice-recv Audio Research

Comprehensive practical research on receiving and sending audio with discord.py and discord-ext-voice-recv.

## 1. discord-ext-voice-recv Architecture

### How It Works

discord-ext-voice-recv extends discord.py to receive audio from Discord voice channels. It mirrors discord.py's `AudioSource` API with an `AudioSink` counterpart.

**Core Components:**
- **VoiceRecvClient**: Custom voice client class that handles incoming audio (replaces default VoiceClient)
- **AudioSink**: Abstract base class for handling incoming audio (mirrors AudioSource but in reverse)
- **Sink Implementations**: BasicSink, WaveSink, FFmpegSink, PCMVolumeTransformer, and filter classes

**Status**: Functionally operational but incomplete—no stability guarantees between updates. IMPORTANT: DAVE protocol (end-to-end encryption) support has limitations; see section 6.

### Connection Pattern

```python
# CONCRETE PATTERN: Connect with VoiceRecvClient
voice_client = await voice_channel.connect(cls=voice_recv.VoiceRecvClient)

# Listen with a sink
voice_client.listen(sink_instance, after=optional_callback)
```

Source: https://github.com/imayhaveborkedit/discord-ext-voice-recv

---

## 2. AudioSink API Specification

### Core Abstract Methods

```python
class AudioSink(ABC):
    def wants_opus(self) -> bool:
        """
        Return True to receive opus-encoded packets
        Return False to receive decoded PCM audio
        WARNING: Don't mix sinks wanting different types in same pipeline
        """
        raise NotImplementedError

    def write(self, user: User | Member | None, data: VoiceData) -> None:
        """
        Primary callback where sink logic executes.

        Args:
            user: Discord User/Member who sent audio (can be None for some packets)
            data: VoiceData container with opus, pcm, and raw packet info
        """
        raise NotImplementedError

    def cleanup(self) -> None:
        """
        Finalizer for resource cleanup (like AudioSource.cleanup())
        """
        raise NotImplementedError
```

### VoiceData Container

The `data` parameter in `write()` has:
- `opus`: Raw Opus-encoded packet (if wants_opus() = True)
- `pcm`: Decoded PCM audio bytes (if wants_opus() = False)
- `user`: The member who sent this data
- `ssrc`: Unique synchronization source ID (maps to specific user across packets)
- Raw RTP/RTCP packet information

### Event Listener Decoration

```python
class MyAudioSink(voice_recv.AudioSink):
    @voice_recv.AudioSink.listener()
    def on_voice_member_disconnect(self, member: discord.Member, ssrc: int):
        """Called when user disconnects from voice"""
        pass

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_start(self, member: discord.Member, ssrc: int):
        """Called when user starts speaking"""
        pass

    @voice_recv.AudioSink.listener()
    def on_rtcp_packet(self, packet_data):
        """Called when RTCP packet received"""
        pass
```

**Important**: These listeners are synchronous (not async) and called from a separate thread.

Sources:
- https://github.com/imayhaveborkedit/discord-ext-voice-recv/blob/main/discord/ext/voice_recv/sinks.py
- https://github.com/imayhaveborkedit/discord-ext-voice-recv/blob/main/README.md

---

## 3. Audio Format Specifications

### Receive Format (What discord-ext-voice-recv Delivers)

**From discord-ext-voice-recv:**
- **PCM Format**: 48 kHz, 16-bit signed (s16le), 2 channels (stereo)
- **Opus Format**: Raw Opus packets (if wants_opus() = True)
- **Frame Size**: 20ms per frame
- **PCM Frame Size**: 3,840 bytes per 20ms frame (48000 Hz * 2 channels * 2 bytes/sample * 0.020s)

**Constants from OpusDecoder** (used in discord-ext-voice-recv):
```python
OpusDecoder.SAMPLING_RATE  # 48000 Hz
OpusDecoder.CHANNELS       # 2 (stereo)
OpusDecoder.SAMPLE_SIZE    # 2 bytes (16-bit)
```

### Playback Format (What discord.py Expects)

**discord.py AudioSource.read() must return:**
- **PCM (if is_opus() returns False)**: 16-bit signed 48 kHz stereo PCM (~3,840 bytes per 20ms frame)
- **Opus (if is_opus() returns True)**: Opus-encoded audio (~20ms frame size, variable bytes)
- **Frame Structure**: Must return exactly 20ms worth of audio per read() call
- **Empty return**: Return empty bytes to signal end of stream

**Key Requirement**: Discord ONLY supports 48 kHz sample rate. Higher sample rates are not supported.

Sources:
- https://github.com/imayhaveborkedit/discord-ext-voice-recv (48 kHz PCM format)
- https://discordpy.readthedocs.io/en/stable/api.html (PCMAudio specification)
- Discord voice API documentation

---

## 4. Per-User Audio vs Mixed Audio Detection

### Per-User Audio Identification

Discord-ext-voice-recv delivers **per-user audio by default**. Each packet's `write()` call includes:

```python
def write(self, user: discord.Member | discord.User | None, data: VoiceData) -> None:
    # user identifies which Discord user sent this audio
    if user is None:
        # System packet or audio without identified user
        return

    user_id = user.id
    ssrc = data.ssrc  # Unique ID for this user's audio stream
    opus_data = data.opus  # Raw opus for this user
    pcm_data = data.pcm    # Decoded PCM for this user
```

### SSRC (Synchronization Source)

- **SSRC**: Unique identifier assigned by Discord to each user's audio stream
- **Lifetime**: Unique across entire voice session for a specific user
- **Usage**: Maps RTP packets to their originating user
- **Identification Flow**:
  1. Discord sends "Speaking" events that cache SSRC → User ID mapping
  2. Audio packets arrive with SSRC header
  3. voice_recv uses SSRC to look up User ID
  4. `write(user=..., data=VoiceData(ssrc=...))` called

### UserFilter for Single-User Reception

```python
# CONCRETE PATTERN: Listen to only one user's audio
import discord
from discord.ext import voice_recv

target_member = ctx.author
user_filter = voice_recv.UserFilter(target_member, sink=my_audio_sink)

await voice_client.listen(user_filter)
```

### Mixed vs Per-User Architecture

- **discord-ext-voice-recv**: Delivers per-user audio via the `user` parameter in `write()`
- **Discord Client Default**: Uses mixed audio (all voices combined into one stream)
- **Your Bot's Capability**: Can choose to:
  - Process each user separately (pass `user` param to separate sinks)
  - Mix users together (implement your own PCM mixing logic)

Sources:
- https://github.com/imayhaveborkedit/discord-ext-voice-recv
- https://github.com/discordjs/voice (SSRCMap reference)
- Discord voice connections documentation

---

## 5. Discord.py Audio Playback (Sending Audio)

### AudioSource API (for sending audio)

Your audio source must implement:

```python
class MyAudioSource(discord.AudioSource):
    def read(self) -> bytes:
        """
        Read 20ms of audio.

        Returns:
            - Opus-encoded bytes if is_opus() returns True
            - PCM bytes (16-bit 48kHz stereo) if is_opus() returns False
            - Empty bytes to signal end of stream
        """
        raise NotImplementedError

    def is_opus(self) -> bool:
        """Return True if audio is Opus-encoded, False if PCM"""
        raise NotImplementedError

    def cleanup(self) -> None:
        """Called when stream finishes or is stopped"""
        raise NotImplementedError
```

### Built-in Audio Sources

#### 1. FFmpegPCMAudio (Most Common)

```python
# CONCRETE PATTERN: Play file with FFmpeg conversion to PCM
import discord

source = discord.FFmpegPCMAudio("path/to/song.mp3")
await voice_client.play(source)
```

**Constructor Signature:**
```python
discord.FFmpegPCMAudio(
    source,                    # File path or stdin source
    executable="ffmpeg",       # Path to ffmpeg executable
    pipe=False,               # True if source is file-like object
    stderr=None,              # Where to send ffmpeg stderr
    before_options=None,      # FFmpeg options before input (e.g., "-reconnect 1")
    options=None              # FFmpeg options after input (e.g., audio filters)
)
```

**Example with options:**
```python
source = discord.FFmpegPCMAudio(
    "https://stream.example.com/audio",
    before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    options="-vn"  # No video
)
await voice_client.play(source)
```

#### 2. PCMAudio (Raw PCM Bytes)

```python
# CONCRETE PATTERN: Play raw PCM from io.BufferedIOBase
import io
import discord

pcm_data = io.BytesIO(raw_pcm_bytes)  # Your 16-bit 48kHz stereo PCM
source = discord.PCMAudio(pcm_data)
await voice_client.play(source)
```

**Constructor Signature:**
```python
discord.PCMAudio(stream)  # io.BufferedIOBase with 16-bit 48kHz stereo PCM
```

**Important**: PCMAudio expects fixed 48 kHz, 16-bit, stereo format. No parameters for sample rate/channels—they're hardcoded.

#### 3. FFmpegOpusAudio (Pre-encoded Opus)

```python
# Skip PCM encoding step—return pre-encoded Opus
source = discord.FFmpegOpusAudio("song.mp3")
# Still outputs Opus, but ffmpeg does the encoding
```

### Volume Control

```python
# CONCRETE PATTERN: Wrap any source with volume control
source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio("song.mp3"))
await voice_client.play(source)

# Later adjust volume (0.0 to 2.0+)
voice_client.source.volume = 0.5
```

### Playing with Error Handler

```python
# CONCRETE PATTERN: Play with after_callback
def after_playback(error):
    if error:
        print(f"Player error: {error}")
    else:
        print("Finished playing")

await voice_client.play(source, after=after_playback)
```

### Key Requirements

- **Opus Library**: To use PCM sources, you need the `opuslib` or `pynacl` library installed
- **FFmpeg**: For FFmpegPCMAudio/FFmpegOpusAudio, ffmpeg must be in PATH
- **Sample Rate**: Discord ONLY accepts 48 kHz. Other rates will fail.
- **Read Thread**: `read()` is called from a separate thread—implement thread-safe logic

Sources:
- https://discordpy.readthedocs.io/en/stable/api.html
- https://github.com/Rapptz/discord.py/blob/master/discord/player.py
- https://fallendeity.github.io/discord.py-masterclass/audio-playback/
- https://github.com/Rapptz/discord.py/blob/master/examples/basic_voice.py

---

## 6. DAVE Protocol Interaction with Voice Receive

### What is DAVE?

**DAVE** (Discord Audio/Video End-to-End Encryption) is Discord's E2EE protocol for voice/video.

**Timeline**:
- **September 2024**: DAVE rollout began for DMs, Group DMs, voice channels
- **March 1, 2026**: CRITICAL - Clients and apps without DAVE support will NO LONGER work with Discord calls

### DAVE Protocol Technical Details

**Encryption Method:**
- Uses **AES-128-GCM** for frame encryption
- **Messaging Layer Security (MLS)** for group key exchange
- Each user derives unique symmetric encryption key per epoch
- Frame encryption happens in WebRTC's encoded transform API before RTP packetization

**Key Rotation:**
- **Per-epoch**: New keys when MLS epoch transitions
- **Per-generation**: Keys rotate after 2^24 frames (using 32-bit nonces)
- **Temporal retention**: Previous generation keys cached ~10 seconds for in-flight packets

**SSRC & User Identification:**
- DAVE uses SSRC to identify which encryption key to apply per frame
- Each Web Worker maintains SSRC → User ID mapping
- User identified by caching SSRC and user IDs from Speaking events

### Impact on discord-ext-voice-recv

**Current Status:**
- discord-ext-voice-recv does NOT fully support DAVE yet
- GitHub issue #38 reports decryption errors with DAVE-encrypted audio
- Error relates to AEAD_XCHACHA20_POLY1305_RTPSIZE codec handling

**What Breaks:**
1. Voice channels with DAVE E2EE enabled (increasingly common)
2. DMs and group DMs (all have DAVE enabled as of 2026)
3. Receiving audio from DAVE-protected voice sessions

**Workarounds (Current):**
- None fully functional. discord-ext-voice-recv needs DAVE support implementation
- Discord provides `libdave` open-source library for implementing DAVE

**Required Changes to discord-ext-voice-recv:**
1. Integrate DAVE decryption in audio reception path
2. Implement MLS group key exchange handling
3. Track per-sender encryption keys
4. Handle key rotation logic
5. Support SSRC → key mapping with MLS epochs

**Resources for Implementation:**
- https://github.com/discord/dave-protocol (Whitepaper)
- https://github.com/discord/libdave (Reference implementation)
- https://discord.com/blog/meet-dave-e2ee-for-audio-video (Overview)

### Implications for Your Project

**Critical**: If targeting Discord voice bots that need to work March 1, 2026 onwards, you MUST implement DAVE support. Current discord-ext-voice-recv will not work with encrypted voice channels.

**Options:**
1. Wait for discord-ext-voice-recv to add DAVE support (no timeline announced)
2. Implement DAVE decryption using libdave library
3. Contribute DAVE support to discord-ext-voice-recv

Sources:
- https://github.com/discord/dave-protocol/blob/main/protocol.md
- https://github.com/imayhaveborkedit/discord-ext-voice-recv/issues/38
- https://github.com/Rapptz/discord.py/issues/9948 (discord.py DAVE support tracking)
- https://discord.com/blog/bringing-dave-to-all-discord-platforms

---

## 7. Complete Code Patterns

### Pattern 1: Receive Per-User Audio as PCM

```python
import discord
from discord.ext import commands, voice_recv
import io

class AudioReceiver(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.audio_buffers = {}  # {user_id: io.BytesIO}

    class PCMSink(voice_recv.AudioSink):
        def __init__(self, cog):
            super().__init__()
            self.cog = cog

        def wants_opus(self) -> bool:
            return False  # Request PCM, not Opus

        def write(self, user: discord.Member | None, data: voice_recv.VoiceData) -> None:
            if user is None or data.pcm is None:
                return

            # Store PCM data per user
            if user.id not in self.cog.audio_buffers:
                self.cog.audio_buffers[user.id] = io.BytesIO()

            self.cog.audio_buffers[user.id].write(data.pcm)

        def cleanup(self) -> None:
            # Save files
            for user_id, buffer in self.cog.audio_buffers.items():
                buffer.seek(0)
                with open(f"user_{user_id}.pcm", "wb") as f:
                    f.write(buffer.read())

    @commands.command()
    async def record(self, ctx):
        vc = await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)
        await vc.listen(self.PCMSink(self))
```

### Pattern 2: Record Individual Users to WAV Files

```python
class WAVRecorder(commands.Cog):
    @commands.command()
    async def record_wavs(self, ctx):
        vc = await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)

        # WaveSink is built-in—writes per-user WAV files
        sink = voice_recv.WaveSink()
        await vc.listen(sink)
```

### Pattern 3: Record Only One Specific User

```python
class SelectiveRecorder(commands.Cog):
    @commands.command()
    async def record_user(self, ctx, user: discord.Member):
        vc = await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)

        # Create a WAV sink, filter to only the target user
        wav_sink = voice_recv.WaveSink()
        user_filter = voice_recv.UserFilter(user, sink=wav_sink)

        await vc.listen(user_filter)
```

### Pattern 4: Play Audio File

```python
class AudioPlayer(commands.Cog):
    @commands.command()
    async def play(self, ctx):
        voice_client = await ctx.author.voice.channel.connect()

        # Pattern A: Simple file
        source = discord.FFmpegPCMAudio("music.mp3")
        await voice_client.play(source)

        # Pattern B: With volume
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio("music.mp3"))
        await voice_client.play(source)

        # Pattern C: From URL with reconnect
        source = discord.FFmpegPCMAudio(
            "https://stream.example.com/audio.mp3",
            before_options="-reconnect 1 -reconnect_delay_max 5"
        )
        await voice_client.play(source, after=lambda e: print(f"Done: {e}"))
```

### Pattern 5: Custom PCM Audio Source (Synthesized)

```python
import struct
import math

class ToneGenerator(discord.AudioSource):
    def __init__(self, frequency=440, sample_rate=48000, duration=5):
        self.frequency = frequency
        self.sample_rate = sample_rate
        self.duration = duration
        self.frame_index = 0
        self.max_frames = int((sample_rate / 1000) * (duration * 1000))

    def read(self) -> bytes:
        if self.frame_index >= self.max_frames:
            return b""  # End of stream

        # Generate 20ms of audio (960 samples at 48kHz)
        samples_per_frame = 960
        frames = []

        for i in range(samples_per_frame):
            sample_index = self.frame_index + i
            t = sample_index / self.sample_rate
            # Generate sine wave
            value = int(32767 * math.sin(2 * math.pi * self.frequency * t))
            # Pack as 16-bit signed, little-endian, stereo (left=right)
            frames.append(struct.pack("<h", value))
            frames.append(struct.pack("<h", value))

        self.frame_index += samples_per_frame
        return b"".join(frames)

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        pass

# Usage
source = ToneGenerator(frequency=440)  # A4 note
await voice_client.play(source)
```

### Pattern 6: Receive Opus → Decode → Play Back

```python
class AudioProcessor(commands.Cog):
    class OpusEchoSink(voice_recv.AudioSink):
        def __init__(self, voice_client):
            super().__init__()
            self.voice_client = voice_client

        def wants_opus(self) -> bool:
            return True  # Get Opus packets

        def write(self, user: discord.Member | None, data: voice_recv.VoiceData) -> None:
            if user is None or data.opus is None:
                return

            # Echo back the Opus data by playing it
            # Note: Real implementation would queue and manage playback
            # This is simplified
            print(f"{user.name} spoke: {len(data.opus)} bytes of Opus")

        def cleanup(self) -> None:
            pass

    @commands.command()
    async def echo_voice(self, ctx):
        vc = await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)
        await vc.listen(self.OpusEchoSink(vc))
```

---

## 8. Frame Timing & Buffering Notes

### Frame Timing

- **Discord Frame Size**: 20 milliseconds
- **Sample Rate**: 48,000 Hz (fixed)
- **Samples per Frame**: 48000 * 0.020 = 960 samples
- **Bytes per Frame (PCM)**: 960 samples * 2 bytes/sample * 2 channels = 3,840 bytes

### Audio Thread Behavior

- `read()` (for sending) is called from a separate thread by discord.py
- `write()` (for receiving) is called from a separate thread by voice_recv
- Both must be thread-safe if accessing shared state

### Buffering for Processing

When receiving audio, buffers fill at 20ms intervals:
```python
# 1 second of audio = 50 frames = 50 * 3840 = 192,000 bytes
bytes_per_second_pcm = 48000 * 2 * 2  # sample_rate * channels * bytes_per_sample
```

---

## 9. Key Takeaways & Gotchas

1. **Sample Rate is Fixed**: 48 kHz. No options. Conversion required for other rates.

2. **DAVE Protocol Critical**: March 1, 2026 deadline. discord-ext-voice-recv needs DAVE support for future compatibility.

3. **Per-User Audio is Default**: discord-ext-voice-recv gives you per-user audio. Mixing is optional.

4. **wants_opus() Consistency**: Don't mix sinks requesting different formats in same pipeline.

5. **Thread Safety**: Both audio send (`read()`) and receive (`write()`) happen in separate threads.

6. **Frame Size Matters**: Always return exactly 20ms of audio. discord.py/voice_recv expect this.

7. **SSRC Identification**: Use SSRC from VoiceData to track audio stream identity across packets.

8. **Cleanup Required**: Implement `cleanup()` to avoid resource leaks (especially for FFmpeg sinks).

9. **PCMAudio Limitations**: No parameter for sample rate/channels—hardcoded 48 kHz stereo.

10. **FFmpeg Dependency**: FFmpegPCMAudio requires ffmpeg installed and in PATH.

---

## 10. References & Sources

Primary sources used:
- https://github.com/imayhaveborkedit/discord-ext-voice-recv (Main library)
- https://github.com/discord/dave-protocol (DAVE protocol whitepaper)
- https://github.com/discord/libdave (DAVE reference implementation)
- https://discordpy.readthedocs.io/ (discord.py documentation)
- https://github.com/Rapptz/discord.py (discord.py source)
- https://discord.com/developers/docs/topics/voice-connections (Discord voice API)
- https://github.com/discordjs/voice (discord.js voice reference—SSRC handling patterns)

