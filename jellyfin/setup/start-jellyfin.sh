#!/bin/bash
# Simple script that starts/restarts the jellyfin service.

sudo systemctl enable jellyfin

if [ -z "$(pgrep jellyfin 2> /dev/null)" ]; then
    sudo service jellyfin start
else
    sudo service jellyfin restart
fi

