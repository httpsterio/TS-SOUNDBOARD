#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import ts3
import ts3.query

PID_FILE = Path(__file__).parent / "bot.pid"


def load_config() -> dict:
    path = Path(__file__).parent / "config.toml"
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        sys.exit("config.toml not found")
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"config.toml parse error: {e}")


def validate_config(cfg: dict) -> None:
    required: list[tuple[str, str, type]] = [
        ("clientquery", "host", str),
        ("clientquery", "port", int),
        ("clientquery", "api_key", str),
        ("bot", "target_user_uid", str),
        ("bot", "clips_dir", str),
        ("bot", "command_prefix", str),
    ]
    for section, key, expected in required:
        if section not in cfg:
            sys.exit(f"config.toml: missing section [{section}]")
        if key not in cfg[section]:
            sys.exit(f"config.toml: missing [{section}] {key}")
        val = cfg[section][key]
        if not isinstance(val, expected):
            sys.exit(f"config.toml: [{section}] {key} must be {expected.__name__}, got {type(val).__name__}")


def check_single_instance() -> None:
    if PID_FILE.exists():
        try:
            existing_pid = int(PID_FILE.read_text().strip())
            os.kill(existing_pid, 0)
            sys.exit(f"Already running with PID {existing_pid}")
        except (ValueError, ProcessLookupError):
            pass  # stale PID file
    PID_FILE.write_text(str(os.getpid()))


def find_clip(clips_dir: Path, name: str) -> Path | None:
    for ext in ("mp3", "ogg", "wav"):
        p = clips_dir / f"{name}.{ext}"
        if p.exists():
            return p
    return None


def main() -> None:
    cfg = load_config()
    validate_config(cfg)

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found in PATH")

    cq_cfg = cfg["clientquery"]
    bot_cfg = cfg["bot"]

    script_dir = Path(__file__).parent
    clips_dir = script_dir / bot_cfg["clips_dir"]
    clips_dir.mkdir(parents=True, exist_ok=True)

    target_uid: str = bot_cfg["target_user_uid"]
    prefix: str = bot_cfg["command_prefix"]
    alsa_device: str = bot_cfg.get("alsa_device", "hw:Loopback,0")

    current_proc: subprocess.Popen | None = None
    current_path: Path | None = None
    play_start_time: float = 0.0
    playback_failed: bool = False
    follow_error_reported: bool = False

    def play(path: Path) -> None:
        nonlocal current_proc, current_path, play_start_time, playback_failed
        if current_proc and current_proc.poll() is None:
            current_proc.terminate()
            current_proc.wait()
        current_path = path
        play_start_time = time.time()
        playback_failed = False
        current_proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-i", str(path), "-f", "alsa", alsa_device],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    check_single_instance()

    try:
        conn = ts3.query.TS3Connection(cq_cfg["host"], cq_cfg["port"])

        try:
            conn.send("auth", common_parameters={"apikey": cq_cfg["api_key"]}, timeout=10)
            conn.send("clientupdate", common_parameters={"client_input_muted": 0, "client_output_muted": 0}, timeout=10)

            for _ in range(10):
                whoami = conn.send("whoami", timeout=10)
                my_clid: str = whoami[0].get("clid", "")
                if my_clid:
                    break
                time.sleep(2)
            else:
                sys.exit("TS3 client has no active server connection after retries")

            conn.send("clientnotifyregister", common_parameters={"schandlerid": 1, "event": "any"}, timeout=10)

            target_clid: str | None = None

            def get_target_cid() -> str | None:
                nonlocal target_clid
                resp = conn.send("clientlist", options=["uid"], timeout=10)
                for c in resp:
                    if c.get("client_unique_identifier") == target_uid:
                        target_clid = c["clid"]
                        return c["cid"]
                target_clid = None
                return None

            def send_text(msg: str) -> None:
                try:
                    cid = conn.send("whoami", timeout=10)[0].get("cid", "")
                    if cid:
                        conn.send("sendtextmessage", common_parameters={"targetmode": 2, "target": cid, "msg": msg}, timeout=10)
                except ts3.query.TS3QueryError:
                    pass

            def follow_target(cid: str | None = None) -> None:
                nonlocal follow_error_reported
                if cid is None:
                    cid = get_target_cid()
                if cid is None:
                    follow_error_reported = False
                    return
                info = conn.send("whoami", timeout=10)
                if info[0].get("cid") == cid:
                    follow_error_reported = False
                    return
                try:
                    conn.send("clientmove", common_parameters={"clid": my_clid, "cid": cid}, timeout=10)
                    follow_error_reported = False
                except ts3.query.TS3QueryError:
                    if not follow_error_reported:
                        follow_error_reported = True
                        send_text("Can't follow: channel is full or requires a password.")

            def check_playback() -> None:
                nonlocal playback_failed
                if current_proc is None or playback_failed:
                    return
                rc = current_proc.poll()
                if rc is not None and rc != 0:
                    playback_failed = True
                    send_text("Playback failed.")

            follow_target()

            while True:
                check_playback()
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
                    if data.get("targetmode") != "2":
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
                        send_text(
                            "!play <name> - play a clip\n"
                            "!stop - stop current clip\n"
                            "!list - list available clips"
                        )

                    elif msg == "!list":
                        seen: set[str] = set()
                        names: list[str] = []
                        for ext in ("mp3", "ogg", "wav"):
                            for p in sorted(clips_dir.glob(f"*.{ext}")):
                                if p.stem not in seen:
                                    seen.add(p.stem)
                                    names.append(p.stem)
                        names.sort()
                        text = "Available clips:\n" + "\n".join(names) if names else "No clips found."
                        send_text(text)

                    elif msg.startswith(prefix + " "):
                        if current_proc is not None and current_proc.poll() is None:
                            continue

                        clip_name = msg[len(prefix) + 1:].strip()
                        if not clip_name:
                            continue
                        if "/" in clip_name or "\\" in clip_name or ".." in clip_name:
                            continue

                        clip = find_clip(clips_dir, clip_name)
                        if clip is None:
                            continue

                        follow_target()
                        play(clip)

        finally:
            if current_proc and current_proc.poll() is None:
                current_proc.kill()
                current_proc.wait()
            conn.close()

    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
