# TS Soundboard Bot

Bot that joins the channel where a target user is, listens for `!play <n>` commands in channel chat, and plays audio clips into the channel via an ALSA loopback sink.

## Prerequisites

- Linux with systemd
- Python 3.11+
- `uv` (for venv management)
- TS3 Linux client installer (`TeamSpeak3-Client-linux_amd64-<version>.run` from teamspeak.com)

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

## 4. Install the TS3 client to `/opt/ts3-client`

Create the directory and give ownership to your user:

```bash
sudo mkdir -p /opt/ts3-client
sudo chown $USER:$USER /opt/ts3-client
```

Download the installer from teamspeak.com (file looks like `TeamSpeak3-Client-linux_amd64-3.6.2.run`). The installer is a self-extracting archive.

```bash
cd /tmp
chmod +x TeamSpeak3-Client-linux_amd64-3.6.2.run
./TeamSpeak3-Client-linux_amd64-3.6.2.run
```

Accept the license (space through, type `y`). It extracts to `/tmp/TeamSpeak3-Client-linux_amd64/`. Move the contents to `/opt/ts3-client`:

```bash
mv /tmp/TeamSpeak3-Client-linux_amd64/* /opt/ts3-client/
```

The two files that matter:

- `/opt/ts3-client/ts3client_linux_amd64` — the binary
- `/opt/ts3-client/ts3client_runscript.sh` — wrapper that sets up Qt library paths, use this to launch

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
DISPLAY=:<N> /opt/ts3-client/ts3client_runscript.sh
```

If there is no desktop session at all, start a temporary Xvfb and VNC into it:

```bash
Xvfb :99 -screen 0 1024x768x24 &
x11vnc -display :99 -nopw -listen localhost &
# then SSH-tunnel 5900 and connect with a VNC client
DISPLAY=:99 /opt/ts3-client/ts3client_runscript.sh
```

In the client:

1. Connect to the server, then **Bookmarks → Add Bookmark**. Enable **Connect on startup**.
2. **Tools → Options → ClientQuery** — copy the API key, you'll need it for `config.toml` later. Leave logging and telnet unchecked.
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

## 7. Deploy the bot to `/opt/ts-soundboard`

Create the directory and give ownership to your user:

```bash
sudo mkdir -p /opt/ts-soundboard
sudo chown $USER:$USER /opt/ts-soundboard
```

Clone the repo into it:

```bash
git clone <repo-url> /opt/ts-soundboard
cd /opt/ts-soundboard
```

Create the venv and install dependencies:

```bash
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
- `ts3client.service` — set `User=`, `Environment=DISPLAY=:99`
- `ts-soundboard.service` — set `User=`

Paths assume `/opt/ts3-client` and `/opt/ts-soundboard`. If you put things elsewhere, update the `WorkingDirectory=` and `ExecStart=` lines to match.

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

Drop audio files into `/opt/ts-soundboard/clips/`. Supported formats: mp3, ogg, wav.

```
clips/
    hello.mp3
    airhorn.ogg
    bruh.wav
```
The bot tries `<name>.mp3` first, then `.ogg`, then `.wav`. New clips are picked up immediately, no restart needed.

### Normalizing clips

The bot transmits clips at whatever level the source files are. Unnormalized clips will be inconsistent and likely too loud. Keep originals in `original-clips/` and normalize them into `clips/`:

```bash
cd /opt/ts-soundboard
mkdir -p original-clips clips
# put raw files in original-clips/ then:
for f in original-clips/*.mp3 original-clips/*.ogg original-clips/*.wav; do
  [ -f "$f" ] || continue
  ffmpeg -y -i "$f" -af loudnorm=I=-30:TP=-1.5:LRA=11 -ar 48000 "clips/$(basename "$f")"
done
```

`I=-30` is a low target suitable for a soundboard mixed into voice chat. Lower the number for quieter, raise for louder.

## Commands

Sent in the TS3 channel chat where the bot is currently located:

- `!play <name>` — play a clip from `clips/`
- `!stop` — stop playback immediately
- `!list` — list available clips
- `!follow` — toggle whether the bot follows the target user across channels (default in `config.toml`)

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

**Clips are way too loud or too quiet**

- Normalize them (see "Normalizing clips" above). The bot's volume setting is a multiplier, not a normalizer.
- Verify TS3 capture preprocessing has AGC disabled: `sqlite3 ~/.ts3client/settings.db "SELECT value FROM Profiles WHERE key='Capture/Default/PreProcessing';" | grep agc`

- Alternatively you can set the volume in config.toml, for clips that are close to hitting 0db the default volume value might be too high.