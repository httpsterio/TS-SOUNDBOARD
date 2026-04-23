#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["yt-dlp"]
# ///
import sys
from pathlib import Path

import yt_dlp

DEST = Path("original-clips")


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: ytdl.py <url> <name>")

    url, name = sys.argv[1], sys.argv[2]
    DEST.mkdir(exist_ok=True)

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(DEST / f"{name}.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": False,
        "noprogress": False,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    print(f"saved: {DEST / f'{name}.mp3'}")


if __name__ == "__main__":
    main()
