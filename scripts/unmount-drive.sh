#!/bin/bash
# Script to unmount a specific drive.

# look for arguments
if [ $# -lt 1 ]; then
    echo "Usage: $0 /path/to/mount/location"
    exit 0
fi

src="$1"
sudo umount ${src}

