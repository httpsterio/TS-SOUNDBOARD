#!/usr/bin/env python3
import subprocess
import time
import tomllib
from pathlib import Path

import ts3
import ts3.query


def load_config() -> dict:
    path = Path(__file__).parent / "config.toml"
    with open(path, "rb") as f:
        return tomllib.load(f)


def find_clip(clips_dir: Path, name: str) -> Path | None:
    for ext in ("mp3", "ogg", "wav"):
        p = clips_dir / f"{name}.{ext}"
        if p.exists():
            return p
    return None


def main() -> None:
    cfg = load_config()
    cq_cfg = cfg["clientquery"]
    bot_cfg = cfg["bot"]

    script_dir = Path(__file__).parent
    clips_dir = script_dir / bot_cfg["clips_dir"]
    target_uid: str = bot_cfg["target_user_uid"]
    prefix: str = bot_cfg["command_prefix"]
    alsa_device: str = bot_cfg.get("alsa_device", "hw:Loopback,0")

    current_proc: subprocess.Popen | None = None
    current_path: Path | None = None
    play_start_time: float = 0.0

    def play(path: Path) -> None:
        nonlocal current_proc, current_path, play_start_time
        if current_proc and current_proc.poll() is None:
            current_proc.terminate()
            current_proc.wait()
        current_path = path
        play_start_time = time.time()
        current_proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-i", str(path), "-f", "alsa", alsa_device],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    conn = ts3.query.TS3Connection(cq_cfg["host"], cq_cfg["port"])

    try:
        conn.send("auth", common_parameters={"apikey": cq_cfg["api_key"]})

        whoami = conn.send("whoami")
        conn.send("clientupdate", common_parameters={"client_input_muted": 0, "client_output_muted": 0})
        my_clid: str = whoami[0]["clid"]

        conn.send("clientnotifyregister", common_parameters={"schandlerid": 1, "event": "any"})

        target_clid: str | None = None

        def get_target_cid() -> str | None:
            nonlocal target_clid
            resp = conn.send("clientlist", options=["uid"])
            for c in resp:
                if c.get("client_unique_identifier") == target_uid:
                    target_clid = c["clid"]
                    return c["cid"]
            target_clid = None
            return None

        def follow_target(cid: str | None = None) -> None:
            if cid is None:
                cid = get_target_cid()
            if cid is None:
                return
            info = conn.send("whoami")
            if info[0].get("cid") == cid:
                return
            try:
                conn.send("clientmove", common_parameters={"clid": my_clid, "cid": cid})
            except ts3.query.TS3QueryError:
                pass

        follow_target()

        while True:
            try:
                event = conn.wait_for_event(timeout=5)
            except ts3.query.TS3TimeoutError:
                follow_target()
                continue

            ename = event.event
            data = event[0]

            if ename == "notifyclientmoved":
                if data.get("clid") == target_clid:
                    follow_target(data.get("ctid"))

            elif ename == "notifycliententerview":
                if data.get("client_unique_identifier") == target_uid:
                    target_clid = data.get("clid")
                    follow_target(data.get("ctid"))

            elif ename == "notifytextmessage":
                if data.get("invokerid") == my_clid:
                    continue

                msg = data.get("msg", "")

                if msg == "!stop":
                    if current_proc and current_proc.poll() is None:
                        current_proc.kill()
                        current_proc.wait()
                        if current_path is not None:
                            elapsed = time.time() - play_start_time
                            current_proc = subprocess.Popen(
                                ["ffmpeg", "-nostdin", "-ss", f"{elapsed:.3f}",
                                 "-i", str(current_path),
                                 "-af", "afade=t=out:st=0:d=0.25", "-t", "0.25",
                                 "-f", "alsa", alsa_device],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )

                elif msg == "!help":
                    cid = conn.send("whoami")[0]["cid"]
                    conn.send("sendtextmessage", common_parameters={"targetmode": 2, "target": cid, "msg": (
                        "!play <name> - play a clip\n"
                        "!stop - stop current clip\n"
                        "!list - list available clips"
                    )})

                elif msg == "!list":
                    seen: set[str] = set()
                    names: list[str] = []
                    for ext in ("mp3", "ogg", "wav"):
                        for p in sorted(clips_dir.glob(f"*.{ext}")):
                            if p.stem not in seen:
                                seen.add(p.stem)
                                names.append(p.stem)
                    names.sort()
                    cid = conn.send("whoami")[0]["cid"]
                    text = "Available clips:\n" + "\n".join(names) if names else "No clips found."
                    conn.send("sendtextmessage", common_parameters={"targetmode": 2, "target": cid, "msg": text})

                elif msg.startswith(prefix + " "):
                    if current_proc is not None and current_proc.poll() is None:
                        continue

                    clip_name = msg[len(prefix) + 1:].strip()
                    if not clip_name:
                        continue

                    clip = find_clip(clips_dir, clip_name)
                    if clip is None:
                        continue

                    follow_target()
                    play(clip)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
