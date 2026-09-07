"""Generate the pipeline's imagery locally with SDXL + SDXL-Lightning.

Run as a subprocess so several gigabytes of weights leave memory the moment the
images exist — the same reason F5-TTS is invoked this way.

    echo '["a cartoon robot, 2D cartoon"]' | python -m scripts.sdxl_generate \
        --out-dir var/youtube/<job> --size 768x1344

Lightning's 4-step LoRA is what makes this practical on an M3 Pro: full SDXL
quality at 1024-class resolution in ~4 steps instead of ~30.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
LIGHTNING_REPO = "ByteDance/SDXL-Lightning"
LIGHTNING_LORA = "sdxl_lightning_4step_lora.safetensors"
STEPS = 4

# SDXL was trained on these buckets; off-bucket sizes produce mangled anatomy and
# duplicated subjects, so the requested size is snapped to the nearest one.
BUCKETS = [
    (1024, 1024), (896, 1152), (832, 1216), (768, 1344), (640, 1536),
    (1152, 896), (1216, 832), (1344, 768), (1536, 640),
]


def snap(width: int, height: int) -> tuple[int, int]:
    target = width / height
    return min(BUCKETS, key=lambda b: abs(b[0] / b[1] - target))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--size", default="768x1344", help="WxH, snapped to an SDXL bucket")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    prompts = json.load(sys.stdin)
    if not prompts:
        print("no prompts", file=sys.stderr)
        return 1

    import torch
    from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline
    from huggingface_hub import hf_hub_download

    device = (
        "mps" if torch.backends.mps.is_available()
        else "cuda" if torch.cuda.is_available()
        else "cpu"
    )
    width, height = snap(*(int(v) for v in args.size.lower().split("x")))
    print(f"device={device} size={width}x{height} images={len(prompts)}", file=sys.stderr)

    started = time.time()
    pipe = StableDiffusionXLPipeline.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float16 if device != "cpu" else torch.float32,
        variant="fp16",
        use_safetensors=True,
    )
    pipe.load_lora_weights(hf_hub_download(LIGHTNING_REPO, LIGHTNING_LORA))
    pipe.fuse_lora()
    # Lightning is distilled for trailing timesteps; the default spacing produces
    # washed-out frames at 4 steps.
    pipe.scheduler = EulerDiscreteScheduler.from_config(
        pipe.scheduler.config, timestep_spacing="trailing"
    )
    pipe = pipe.to(device)
    pipe.set_progress_bar_config(disable=True)
    # Unified memory is shared with everything else on the machine. Without slicing,
    # attention at SDXL resolutions pages to disk and each image gets slower than the
    # last — measured 61s, then 488s, then 365s on an 18 GB M3 Pro.
    pipe.enable_attention_slicing()
    # VAE slicing moved from the pipeline onto the VAE across diffusers versions.
    for enable in (
        getattr(pipe, "enable_vae_slicing", None),
        getattr(pipe.vae, "enable_slicing", None),
    ):
        if callable(enable):
            enable()
            break
    print(f"model loaded in {time.time() - started:.0f}s", file=sys.stderr)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for i, prompt in enumerate(prompts):
        began = time.time()
        generator = torch.Generator(device="cpu").manual_seed(args.seed + i)
        image = pipe(
            prompt=f"{prompt}, highly detailed, sharp focus",
            negative_prompt="text, watermark, signature, blurry, low quality, "
            "deformed, extra limbs",
            num_inference_steps=STEPS,
            guidance_scale=0.0,  # Lightning is distilled without CFG
            width=width,
            height=height,
            generator=generator,
        ).images[0]
        path = args.out_dir / f"image{i}.png"
        image.save(path)
        written.append(str(path))
        # Reclaim between images; the MPS allocator otherwise grows until it pages.
        if device == "mps":
            torch.mps.empty_cache()
        elif device == "cuda":
            torch.cuda.empty_cache()
        print(f"[{i + 1}/{len(prompts)}] {time.time() - began:.0f}s {path}", file=sys.stderr)

    json.dump(written, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
