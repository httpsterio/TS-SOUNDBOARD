#!/usr/bin/env -S uv run --script
import subprocess
import sys
from pathlib import Path

EXTENSIONS = ("mp3", "ogg", "wav")
SOURCE = Path("original-clips")
DEST = Path("clips")
TARGET_LUFS = -30


def main() -> None:
    if not SOURCE.is_dir():
        sys.exit(f"missing directory: {SOURCE}")
    DEST.mkdir(exist_ok=True)

    existing = {p.stem for p in DEST.iterdir() if p.suffix[1:] in EXTENSIONS}

    sources = [p for ext in EXTENSIONS for p in SOURCE.glob(f"*.{ext}")]
    todo = [p for p in sources if p.stem not in existing]

    if not todo:
        print("nothing to do")
        return

    for src in todo:
        dst = DEST / src.name
        print(f"normalizing {src.name}")
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(src),
                "-af", f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11",
                "-ar", "48000",
                str(dst),
            ],
            check=True,
        )

    print(f"done: {len(todo)} file(s)")


if __name__ == "__main__":
    main()
