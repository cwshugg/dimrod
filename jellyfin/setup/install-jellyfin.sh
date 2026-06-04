#!/bin/bash
# Simple helper script to install Jellyfin.
#
# https://jellyfin.org/docs/general/installation

# Install needed dependencies
sudo apt install curl

# Download the installer script and verify it
script_url="https://repo.jellyfin.org/install-debuntu.sh"
sum_url="${script_url}.sha256sum"
script_name="$(basename "${script_url}")"
sum_name="$(basename "${sum_url}")"
curl -s "${script_url}" -O && \
    curl -s "${sum_url}" -O && \
    sha256sum -c "${sum_name}"

# Execute the installer script
script_name="$(basename "${script_url}")"
if [ -f "${script_name}" ]; then
    chmod +x "${script_name}"
    echo "Executing jellyfin installer script..."
    sudo bash ./"${script_name}"
else
    echo "Failed to download the installer script."
fi

# Remove the files
rm -f "${script_name}" "${sum_name}"

