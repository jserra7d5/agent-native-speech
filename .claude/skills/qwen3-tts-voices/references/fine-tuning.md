# Fine-Tuning and Streaming

## Fine-Tuning Qwen3-TTS

When voice cloning from a single reference clip does not capture a speaker well enough, fine-tuning the Base model on multiple samples from that speaker produces a specialized checkpoint with significantly better voice reproduction.

### Community Fine-Tuning Tool

**Repository**: https://github.com/sruckh/Qwen3-TTS-finetune

This is a community-maintained tool that wraps the fine-tuning process into a simple CLI workflow.

### Installation

```bash
git clone https://github.com/sruckh/Qwen3-TTS-finetune.git
cd Qwen3-TTS-finetune
pip install -r requirements.txt
```

### Dataset Preparation

Prepare a directory of clean WAV files from a single speaker.

**Requirements**:
- 10-100 WAV files from a single speaker
- 5-30 seconds each
- Clean audio (no background noise, music, reverb, or other speakers)
- Consistent recording conditions (same microphone, room, and volume)
- One reference audio clip to use as the voice identity anchor

**Recommended dataset sizes**:

| Samples | Quality | Training Time (A100) |
|---------|---------|---------------------|
| 10-20 | Acceptable | ~15-30 min |
| 30-50 | Good | ~30-60 min |
| 50-100 | Best | ~1-2 hours |

**Directory structure**:

```
audio/
  sample_01.wav
  sample_02.wav
  ...
  sample_50.wav
ref.wav           # reference clip for voice identity
```

The tool auto-transcribes each audio file using Whisper. You do not need to provide transcripts manually, but you can provide them in a `metadata.csv` if you have them:

```csv
file,text
sample_01.wav,Hello this is a sample recording for voice training.
sample_02.wav,The quick brown fox jumps over the lazy dog.
```

### Training

**One-command training**:

```bash
./train.sh \
  --audio_dir ./audio \
  --ref_audio ./ref.wav \
  --speaker_name my_voice \
  --epochs 3 \
  --batch_size 2 \
  --learning_rate 1e-5
```

**Key parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--audio_dir` | (required) | Directory containing training WAV files |
| `--ref_audio` | (required) | Reference audio for voice identity |
| `--speaker_name` | (required) | Name for the output checkpoint |
| `--epochs` | 3 | Number of training epochs. 2-5 is typical. |
| `--batch_size` | 2 | Batch size. Reduce to 1 if OOM. |
| `--learning_rate` | 1e-5 | Learning rate. Lower (5e-6) for fewer samples. |
| `--base_model` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | Base model to fine-tune |
| `--output_dir` | `./output` | Where to save checkpoints |
| `--save_every_epoch` | true | Save checkpoint after each epoch |

**GPU requirements**:

| Model | Minimum VRAM | Recommended |
|-------|-------------|-------------|
| 0.6B-Base | ~8GB | 12GB+ |
| 1.7B-Base | ~16GB | 24GB+ |

### Output

Training produces checkpoints in the output directory:

```
output/
  checkpoint-epoch-1/
  checkpoint-epoch-2/
  checkpoint-epoch-3/
```

Each checkpoint is a full model directory that can be loaded directly with `Qwen3TTSModel.from_pretrained()`.

### Inference with Fine-Tuned Model

```python
import torch
from qwen_tts import Qwen3TTSModel

# Load the fine-tuned checkpoint
model = Qwen3TTSModel.from_pretrained(
    "output/checkpoint-epoch-2",
    device_map="cuda:0",
    dtype=torch.bfloat16,
)

# Use voice cloning API with the fine-tuned model
# The fine-tuned model still needs a reference audio for clone prompting,
# but it will reproduce the voice much more accurately.
prompt = model.create_voice_clone_prompt(
    ref_audio="ref.wav",
    ref_text="The reference text spoken in the clip.",
)

wavs, sr = model.generate_voice_clone(
    text="Hello, this is my fine-tuned voice!",
    language="English",
    voice_clone_prompt=prompt,
)
```

### Using a Fine-Tuned Model in agent-native-speech

To use a fine-tuned checkpoint in agent-native-speech, update the Base model ID in `server/tts_engine.py`:

```python
# Replace the default HuggingFace ID with your checkpoint path
BASE_MODEL_ID: str = "/path/to/output/checkpoint-epoch-2"
```

Everything else (voice profiles, prompt caching, generation parameters) works the same. The engine loads your fine-tuned checkpoint instead of the base model from HuggingFace.

### Fine-Tuning Tips

- **Start with epoch 2**: Epoch 1 often underfits; epoch 3+ risks overfitting on small datasets. Test epoch 2 first.
- **Listen to each epoch**: Generate test audio from each checkpoint to find the sweet spot between voice fidelity and naturalness.
- **Avoid overfitting**: Signs include robotic/flat delivery, repeated syllables, or the model reproducing training utterances verbatim. Reduce epochs or increase dataset size.
- **Quality over quantity**: 20 clean recordings beat 100 noisy ones. Remove any samples with background noise, overlapping speech, or recording artifacts.
- **Consistent conditions**: Record all samples in the same session if possible. Varying microphones, rooms, or vocal states confuses the model.

## Streaming (Community Fork)

The official `qwen-tts` package generates complete audio before returning. A community fork adds token-level streaming for significantly lower latency.

**Repository**: https://github.com/dffdeeq/Qwen3-TTS-streaming

### What It Provides

- `stream_generate_pcm()` -- streaming synthesis for preset/designed voices
- `stream_generate_voice_clone()` -- streaming synthesis for cloned voices
- Approximately **6x performance improvement** over the official non-streaming generation
- Audio begins playing while the model is still generating subsequent tokens

### Installation

```bash
pip install git+https://github.com/dffdeeq/Qwen3-TTS-streaming.git
```

Or clone and install locally:

```bash
git clone https://github.com/dffdeeq/Qwen3-TTS-streaming.git
cd Qwen3-TTS-streaming
pip install -e .
```

### API Usage

```python
import torch
from qwen3_tts_streaming import Qwen3TTSStreaming

model = Qwen3TTSStreaming.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    device_map="cuda:0",
    dtype=torch.bfloat16,
)

# Streaming voice clone
prompt = model.create_voice_clone_prompt(
    ref_audio="ref.wav",
    ref_text="Reference text here.",
)

for pcm_chunk in model.stream_generate_voice_clone(
    text="Hello, this is streaming voice synthesis!",
    language="English",
    voice_clone_prompt=prompt,
):
    # pcm_chunk is a numpy array of audio samples
    # Feed to audio player, Discord source, etc.
    play_audio(pcm_chunk)
```

### Integration with agent-native-speech

agent-native-speech already has a streaming architecture (`StreamingAudioSource` in `server/audio_source.py`) that feeds audio chunks to Discord while synthesis continues. The current implementation streams at the sentence level (one chunk per preprocessed sentence).

To integrate the streaming fork for token-level streaming:

1. Replace `qwen-tts` with the streaming fork in dependencies
2. Modify `TTSEngine._synthesize_chunk()` to use `stream_generate_voice_clone()` or `stream_generate_pcm()`
3. Yield PCM chunks through `StreamingAudioSource.add_segment()` as they arrive
4. This would reduce time-to-first-audio from "full sentence synthesis time" to "a few hundred milliseconds"

This integration is not yet implemented but would provide the largest latency improvement for long utterances.

### Streaming vs Non-Streaming Comparison

| Metric | Non-Streaming (official) | Streaming (fork) |
|--------|-------------------------|-----------------|
| Time to first audio | Full synthesis time | ~200-500ms |
| Total generation time | Baseline | Similar or slightly longer |
| Audio quality | Reference quality | Comparable |
| API compatibility | `generate_voice_clone()` | `stream_generate_voice_clone()` |
| Prompt caching | Supported | Supported |
