#!/bin/bash
# A simple script to help me install phile and configure it the way I want.
#   https://pi-hole.net/
#   https://docs.pi-hole.net/main/basic-install/

server_root=/var/www/pihole
if [ ! -z "$(which pihole 2> /dev/null)" ]; then
    # before we invoke the installer, we need to temporarily move my home server
    # files out of /var/www/html (lighttpd by default installs there)
    hs_tmpdir=~/.hs_root
    hs_root=/var/www/html
    if [ ! -d ${hs_tmpdir} ]; then
        sudo mkdir ${hs_tmpdir}
    fi
    if [ ! -z "$(ls ${hs_root})" ]; then
        echo "Temporarily moving home server root files to ${hs_tmpdir}..."
        sudo mv ${hs_root}/* ${hs_tmpdir}/
    fi
    
    # invoke the isntall from across the internet
    installer_url=https://install.pi-hole.net
    curl -sSL ${installer_url} | sudo bash

    # now, we'll create the true pihole server directory and move the installed
    # files over
    echo "Moving pihole server files to ${server_root}..."
    if [ ! -d ${server_root} ]; then
        sudo mkdir ${server_root}
    fi
    sudo mv ${hs_root}/* ${server_root}/

    # move the original files back and remove the tmpdir
    if [ ! -z "$(ls ${hs_tmpdir})" ]; then
        echo "Resotring home server files to ${hs_root}..."
        sudo mv ${hs_tmpdir}/* ${hs_root}/
    fi
    sudo rm -rf ${hs_tmpdir}
fi


# add a line to the external config that force a port change
config_path=/etc/lighttpd/external.conf
server_port=2301
if [ -z "$(cat ${config_path} | grep 'port')" ]; then
    echo "Setting pihole admin server port to ${server_port}..."
    echo "server.port := ${server_port}" | sudo tee -a ${config_path} > /dev/null
fi

# force a root change
if [ -z "$(cat ${config_path} | grep 'document-root')" ]; then
    echo "Setting pihole admin server root directory to ${server_root}..."
    echo "server.document-root := \"${server_root}\"" | sudo tee -a ${config_path} > /dev/null
fi

