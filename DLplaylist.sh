#!/usr/bin/env bash

# Resolve the directory where this launcher script (and DLplaylist.png / .rasi) resides
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Change directory to ensure relative paths in the .rasi file resolve correctly
cd "$SCRIPT_DIR"

# Launch Rofi using relative path
URL=$(rofi -dmenu -p "Playlist:" -theme "./DLplaylist.rasi")

if [ -z "$URL" ]; then
    exit 0
fi

# Run the python script via uv in the project directory
kitty --detach --directory "$SCRIPT_DIR" uv run "$SCRIPT_DIR/playlistDL.py" "$URL"