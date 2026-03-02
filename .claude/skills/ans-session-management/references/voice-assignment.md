# Voice Assignment

## VoicePool (`server/voice_pool.py`)

Manages per-session TTS voice assignment so concurrent agents sound distinct.

### Construction

```python
VoicePool(
    pool_voices=["Ryan", "Aiden", "Vivian", ...],  # Ordered assignment list
    default_voice="Ryan",                            # Single-session fallback
    system_voice="system",                           # Reserved, never assigned
)
```

`VoicePool.from_config(config)` reads from `Config`:
- `config.voice_pool` -> `pool_voices`
- `config.tts.default_voice` -> `default_voice`
- `config.system_voice` -> `system_voice`

If `pool_voices` is empty, falls back to `_DEFAULT_POOL_VOICES`:
```python
["Ryan", "Aiden", "Vivian", "Serena", "Dylan", "Eric"]
```

The system voice is filtered out of the pool list during construction.

### Internal State

```python
_default_voice: str                    # Voice for single-session mode
_system_voice: str                     # Reserved voice name
_pool: list[str]                       # Ordered pool (system voice excluded)
_assignments: dict[str, str]           # session_id -> voice_name
```

### Assignment Algorithm

`assign_voice(session_id, requested_voice=None)` returns a voice name:

1. **Already assigned**: If `session_id` is in `_assignments`, return existing voice. Idempotent.
2. **Explicit request honored**: If `requested_voice` is provided, not the system voice, and not already assigned to another session, assign it.
3. **Explicit request denied**: If requested voice is the system voice or already in use, log warning and fall through to pool.
4. **Pool scan**: Iterate `_pool` in order. First voice not in `assigned_voices` set gets assigned.
5. **Pool exhausted**: All voices in use. Pick the pool voice with the fewest current assignments (spread reuse). Log warning.

### Resolution (Single vs Multi)

`resolve_voice(session_id)` determines the *effective* voice at synthesis time:

```python
def resolve_voice(self, session_id: str) -> str:
    if len(self._assignments) <= 1:
        return self._default_voice
    return self._assignments.get(session_id, self._default_voice)
```

This means:
- When only one agent is connected, it always speaks with `default_voice` regardless of what was assigned.
- When two or more agents are connected, each uses its pool-assigned voice.
- The transition happens automatically as sessions register/unregister.

### Release

`release_voice(session_id)` removes the assignment. Returns the voice name or None.

### System Voice

`get_system_voice()` returns the voice for switchboard announcements. Falls back to `default_voice` if no system voice is configured.

### Properties

- `default_voice` -- the single-session voice
- `pool_voices` -- copy of the ordered pool list
- `assignments` -- copy of current session->voice mapping
- `active_session_count` -- number of assigned sessions

## VoiceProfileRegistry (`server/voice_profile.py`)

Manages the mapping from voice names to actual TTS parameters. Separate from VoicePool -- the pool assigns names, the registry resolves names to synthesis parameters.

### VoiceProfile Dataclass

```python
@dataclass
class VoiceProfile:
    name: str                          # Internal name (e.g., "Ryan")
    display_name: str                  # Human-readable name
    profile_type: str                  # "preset" or "clone"
    language: str                      # Native language
    speaker: str | None = None         # Preset speaker name (Qwen3-TTS)
    ref_audio_path: Path | None = None # Clone reference audio file
    ref_text: str | None = None        # Clone reference transcript
    x_vector_only: bool = False        # Clone: use x-vector extraction only
```

### Preset Speakers

Built-in Qwen3-TTS CustomVoice speakers, registered at init:

| Name | Language |
|---|---|
| Vivian | Chinese |
| Serena | Chinese |
| Uncle_Fu | Chinese |
| Dylan | Chinese |
| Eric | Chinese |
| Ryan | English |
| Aiden | English |
| Ono_Anna | Japanese |
| Sohee | Korean |

All presets have `profile_type="preset"` and `speaker` set to their name.

### Clone Profiles

Scanned from `voices/<name>/profile.json` at registry construction.

Profile JSON format:
```json
{
    "name": "my-clone",
    "display_name": "My Clone Voice",
    "language": "English",
    "ref_audio": "reference.wav",
    "ref_text": "The reference transcript spoken in the wav file.",
    "x_vector_only": false
}
```

Rules:
- `ref_audio` path is relative to the profile directory, resolved to absolute.
- If `ref_audio` file does not exist, profile is skipped with warning.
- If `x_vector_only` is false, `ref_text` is required. Empty text causes skip.
- Profile directory structure: `voices/<name>/profile.json` + `reference.wav`.

### Registry API

```python
registry = VoiceProfileRegistry(tts_config)
profile = registry.get("Ryan")        # Raises KeyError if not found
profiles = registry.list_profiles()    # All registered profiles
"Ryan" in registry                     # Containment check
```

## ElevenLabs Voice Mapping

When using the ElevenLabs TTS backend, voice names must map to ElevenLabs voice IDs. This mapping is configured in `config.json`:

```json
{
    "tts": {
        "elevenlabs": {
            "voices": {
                "Ryan": "CYDzJWiIyIiQuhRB4r1K",
                "Aiden": "pNInz6obpgDQGcFmaJgB",
                "system": "21m00Tcm4TlvDq8ikWAM"
            }
        }
    }
}
```

The VoicePool assigns voice *names* (e.g., "Ryan"). The ElevenLabs TTS backend resolves the name to a voice ID at synthesis time using this mapping. The VoiceProfileRegistry is only used by the local Qwen3-TTS backend.

## Integration Flow

```
register_session(requested_voice="Aiden")
    |
    v
VoicePool.assign_voice("session-uuid", "Aiden")
    -> checks availability, assigns "Aiden"
    |
    v
(later) initiate_call(session_id=...)
    |
    v
SessionManager.resolve_voice(session_id)
    -> VoicePool.resolve_voice(session_id)
    -> if single session: return default_voice
    -> if multi session: return "Aiden"
    |
    v
CallManager.initiate_call(voice="Aiden")
    -> TTSBackend.synthesize(text, voice="Aiden")
    -> ElevenLabs: looks up "Aiden" -> "pNInz6obpgDQGcFmaJgB"
    -> Local Qwen3: VoiceProfileRegistry.get("Aiden") -> preset speaker
```
