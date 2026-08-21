![playlistDL.py](DLplaylist.png)
# playlistDL.py

A robust Python utility for downloading playlists from SoundCloud and YouTube, including complete metadata extraction and preservation.

## Overview

`playlistDL.py` streamlines the process of archiving audio and video content from popular streaming platforms. 
Whether you're curating a courese, podcasts, or conducting research, this tool efficiently handles bulk playlist downloads while maintaining comprehensive metadata.

## Features

- **Multi-Platform Support**: Download playlists from both SoundCloud and YouTube
- **Metadata Extraction**: Automatically captures and preserves Meta Data in duckdb
- **Batch Processing**: Handle entire playlists with a single command
- **Error Handling**: Robust error recovery for network interruptions and invalid tracks

## Requirements

- install uv
- Cookie auth files for all required services to avoid problems

## Instal Dependancies
 
- uv 

for running python script and installing dependacies

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

- deno

JS Runtime

```
curl -fsSL https://deno.land/install.sh | sh
```

- gui for script 

rofi and kitty on Ubuntu/Debian

```
sudo apt update && sudo apt install rofi
```

rofi and kitty on MacOS

```
brew install rofi
brew install --cask kitty
```

## Usage

Basic usage:

uv handles all the dependancies for this script, 
refresh will update the yt-dlp so youtube downloading will keep downloading

```bash
uv run --refresh playlistDL.py [PLAYLIST_URL] 
```

Rofi GUI:

```bash
./DLplaylist.sh
```

It is programed to download to ./media/youtube and ./media/soundcloud depending on your url.
options are also in the script to ensure the best results

## Configuration

Modify the script parameters to customize:
- Output directory
- Audio format and quality
- Retry behavior

## License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0). See LICENSE file for details.

## TODO:

- Add a rofi script for a GUI front end to this download script.
- Build a script that uses the Meta Data from duckdb for an a player


# ========================================================

## Cookies

### local utility

If you're using it as a local utility, then keep the 

```
"cookiesfrombrowser": ("firefox", ),
```

Firefox is the best option, because it can stay open and read your cookies locally.
This should also work in MacOS.

Otherwise you can follow the advice bellow for exporting your cookies from the browswer.


### How to Export Cookies for YouTube and SoundCloud

#### 🛠️ Best Practices (Recommended)
1. **Use an incognito/private window** to avoid cookie conflicts.
2. **Log in** to both YouTube and SoundCloud.
3. **Visit `https://www.youtube.com/robots.txt`** to stop YouTube from rotating your session cookies.
4. Use a **browser extension** to export cookies in Netscape format.

#### 🔹 For Chrome/Edge:
- Install: [Get cookies.txt LOCALLY][chrome-ext]
- Go to `youtube.com` and `soundcloud.com` (logged in)
- Click the extension icon → **Export** → saves `cookies.txt`

#### 🔹 For Firefox:
- Install: [cookies.txt][firefox-ext]
- Visit both sites while logged in
- Click extension → **Current site** → Export

#### ✅ Final Cookie File
Ensure your `cookies.txt` includes entries for:
- `.youtube.com`
- `.google.com`
- `accounts.google.com`
- `.soundcloud.com`

> ⚠️ **Never manually edit or combine cookies** — use the extension to export from a single tab where you visited both services.

[chrome-ext]: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/fngmhnnpilhplaeedifhccceomclgfbg
[firefox-ext]: https://addons.mozilla.org/firefox/addon/cookies-txt/   
   
**Note** this is advice from Brave Leo AI, i did combine my cookies for youtube and it worked.