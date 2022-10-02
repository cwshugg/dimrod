#!/bin/bash
# Helper script that follows along with this guide to install nginx:
#
#   https://ubuntu.com/tutorials/install-and-configure-nginx
#
# I'm using nginx as my home HTTP server.

# install updates and nginx (use -y to skip the 'are you sure?' prompts)
sudo apt update -y
sudo apt install nginx -y

# by default, HTML files are in /var/www/html. Navigate to this directory
root_dir=/var/www
if [ ! -d ${root_dir} ]; then
    echo "Cannot find root nginx directory: ${root_dir}"
    exit 1
fi
cd ${root_dir}

# I'll set up my own directory to contain my home server HTML files
hs_dir=${root_dir}/chs
if [ -d ${hs_dir} ]; then
    echo "Home server root directory already exists: ${hs_dir}"
else
    echo "Creating server root directory: ${hs_dir}/ ..."
    sudo mkdir ${hs_dir}
fi

# next, we'll set up virtual hosting. Copy my config file to the correct spot
hs_init_path=$(dirname $(find ${HOME} -name "$(basename $0)"))
if [ -z "${hs_init_path}" ]; then
    echo "Couldn't find the location of this script. Unable to continue setup."
    exit 1
fi
hs_vhost_path=${hs_init_path}/chs.vhost
vhost_dest=/etc/nginx/sites-enabled/chs
echo "Copying virtual host file from ${hs_vhost_path} to ${vhost_dest} ..."
sudo cp ${hs_vhost_path} /etc/nginx/sites-enabled/chs

# next, copy all my HTML files from this repository to the directory
hs_root_path=$(dirname ${hs_init_path})/root
echo "Copying server root files from ${hs_root_path} to ${hs_dir}/ ..."
sudo cp -r $(realpath ${hs_root_path})/* ${hs_dir}/

