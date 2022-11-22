#!/bin/bash
# Script to mount a drive to a specific location.

# look for arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 /dev/DISK /path/to/mount/location"
    exit 0
fi

src="$1"
dst="$2"

# if the directory doesn't exist, make it
if [ ! -d ${dst} ]; then
    sudo mkdir ${dst}
fi

# mount it
sudo mount ${src} ${dst}

