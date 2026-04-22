# TS Soundboard Bot

Bot that joins the channel where a target user is, listens for `!play <name>` commands in channel chat, and plays audio clips into the channel via an ALSA loopback sink.

## Prerequisites

- Linux with systemd
- Python 3.11+
- `uv` (for venv management)
- A TS3 Linux client installed somewhere accessible

## 1. Install system packages

```bash
sudo apt install xvfb ffmpeg alsa-utils
```

## 2. User must be in the `audio` group

ALSA devices are restricted to the `audio` group. Without membership, `aplay -l` returns "no soundcards found" even when the hardware is present.

Check:

```bash
groups
```

If `audio` is missing:

```bash
sudo usermod -aG audio $USER
```

Log out and back in for this to apply to login sessions. For the current shell only, use `newgrp audio`.

## 3. Load the ALSA loopback module

The bot plays audio into a virtual loopback device that the TS3 client captures as its mic input.

Check if loaded:

```bash
lsmod | grep snd_aloop
cat /proc/asound/cards
```

If missing, load and persist:

```bash
sudo modprobe snd-aloop
echo "snd-aloop" | sudo tee /etc/modules-load.d/snd-aloop.conf
```

After loading, `aplay -l` should list a `Loopback` card. Note the card number (e.g. `card 2`). The playback device will be `plughw:<N>,0` and the capture device `plughw:<N>,1`.

## 4. Install the TS3 client

Download the Linux client from teamspeak.com and extract it. Any path works, just remember it for the service file later.

If `./ts3client_linux_amd64` fails with a missing library error, install it:

```bash
sudo apt install libevent-2.1-7
```

The exact package name may differ by distro version.

## 5. Configure the TS3 client (one-time, via GUI)

The TS3 client stores its config in `~/.ts3client/settings.db`. This must be set up once interactively before the headless service can use it.

If the machine has a desktop session running, find its display number:

```bash
ps aux | grep -i xorg
```

Look for the `:N` in the Xorg process args. Launch the client into that display:

```bash
DISPLAY=:<N> /path/to/ts3-client/ts3client_runscript.sh
```

If there is no desktop session at all, start a temporary Xvfb and VNC into it:

```bash
Xvfb :99 -screen 0 1024x768x24 &
x11vnc -display :99 -nopw -listen localhost &
# then SSH-tunnel 5900 and connect with a VNC client
DISPLAY=:99 /path/to/ts3-client/ts3client_runscript.sh
```

In the client:

1. Connect to the server, then **Bookmarks → Add Bookmark**. Enable **Connect on startup**.
2. **Tools → Options → ClientQuery** — copy the API key into `config.toml` as `api_key`. Leave logging and telnet unchecked.
3. **Tools → Options → Capture** — set mode to ALSA, device to the loopback capture (e.g. `plughw:2,1` where `2` is your loopback card number from step 3). Set activation mode to **Continuous**.
4. On the same Capture screen, disable VAD, denoiser, echo cancellation, AGC, typing suppression — everything.
5. **Tools → Options → Playback** — set any ALSA device. The bot doesn't need to hear, but if this is left empty TS3 may mute itself on startup defensively.

Close the client.

## 6. Pick a display number for the headless service

Xvfb needs a display number that isn't already in use. Check what's taken:

```bash
ls /tmp/.X*-lock 2>/dev/null
```

Any file like `.X1-lock` means display `:1` is in use. Pick a free number (`:99` is a safe default). Use the same number in both `xvfb.service` and `ts3client.service`.

## 7. Install the bot

```bash
cd /opt/ts-soundboard
uv venv
uv pip install ts3
```

Copy the example config and fill in the blanks:

```bash
cp config.toml.example config.toml
```

- `api_key` — from step 5 (TS3 client Tools → Options → ClientQuery)
- `target_user_uid` — base64 UID from the TS3 client info dialog (right-click user → Info), ends with `=`
- `alsa_device` — loopback playback device, `plughw:<N>,0` where `<N>` is your loopback card from `aplay -l`

## 8. Install and enable the services

Edit the three `.service` files in this repo:

- `xvfb.service` — set `User=`, pick the display number (`:99`)
- `ts3client.service` — set `User=`, `Environment=DISPLAY=:99`, `WorkingDirectory=` to the TS3 client path, `ExecStart=` to the runscript path
- `ts-soundboard.service` — set `User=`, `WorkingDirectory=` and `ExecStart=` paths

Then:

```bash
sudo cp xvfb.service ts3client.service ts-soundboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb ts3client ts-soundboard
```

## 9. Verify

```bash
systemctl status xvfb ts3client ts-soundboard
journalctl -u ts-soundboard -f
```

The bot logs should show it connecting to ClientQuery and finding the target user. In TS, the bot should appear connected, unmuted, and following the target user across channels.

## Adding clips

Drop audio files into `clips/`. Supported formats: mp3, ogg, wav.

```
clips/
  hello.mp3
  airhorn.ogg
  bruh.wav
```

Trigger from TS3 channel chat:

```
!play hello
!play airhorn
```

The bot tries `<name>.mp3` first, then `.ogg`, then `.wav`. If none exist, the command is silently ignored.

New clips are picked up immediately. No restart needed.

## Troubleshooting

**Bot is muted in TS**

- Check `aplay -l` reports the Loopback card. If not, user is missing from `audio` group or `snd-aloop` isn't loaded.
- Check `~/.ts3client/settings.db` has both Capture and Playback devices configured: `sqlite3 ~/.ts3client/settings.db "SELECT * FROM Profiles;"`

**TS3 client fails to start**

- `DISPLAY` in the service file must match the Xvfb display number.
- Check `journalctl -u xvfb` and `journalctl -u ts3client`.

**Bot can't connect to ClientQuery**

- API key in `config.toml` must match the one in Tools → Options → ClientQuery.
- TS3 client must be fully started before the bot. The `After=ts3client.service` in `ts-soundboard.service` handles ordering, and `ExecStartPre=/bin/sleep 2` in `ts3client.service` gives Xvfb time to be ready.

**Nothing plays into the channel**

- `alsa_device` in `config.toml` must point to loopback playback (`plughw:<N>,0`).
- TS3's Capture device must be the matching loopback capture (`plughw:<N>,1`).
- Card number `<N>` comes from `aplay -l`.