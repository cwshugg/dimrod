#!/bin/bash
# Simple script that starts/restarts the jellyfin service.

# check for the mount directory - create it if it doesn't exist
mount_dir=/mnt/jellyfin_0
mount_drive=/dev/sdb1
if [ ! -d ${mount_dir} ]; then
    sudo mkdir ${mount_dir}
fi
# if the directory is empty or we can't find a mount, mount it
if [ -z "$(mount | grep "${mount_dir}")" ] || [ -z "$(ls ${mount_dir})" ]; then
    sudo mount ${mount_drive} ${mount_dir}
fi

sudo systemctl enable jellyfin

if [ -z "$(pgrep jellyfin 2> /dev/null)" ]; then
    sudo service jellyfin start
else
    sudo service jellyfin restart
fi

