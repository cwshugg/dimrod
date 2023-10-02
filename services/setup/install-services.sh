#!/bin/bash
# Helper script to install DImROD's services as systemd services.
# Installing these will ensure each service is restarted on boot.

# locate the directory in which the services are located
service_dir="$(realpath $(dirname $(dirname "$0")))"

# use a username for prefixes for all service files (change this as needed)
user="cwshugg"

# if no arguments were given, show a help message
if [ $# -lt 1 ]; then
    echo "This will install DImROD's services as systemd background services."
    echo "Please enter the names of the services you wish to enable as command-line arguments."
    exit 0
fi

# iterate over all command-line arguments, which should each be a service name
for service in "$@"; do
    # form a path to the service directory; complain if not found
    dpath="${service_dir}/${service}"
    if [ ! -d "${dpath}" ]; then
        echo "Error: could not find service called \"${service}\". Exiting."
        exit 1
    fi

    # look for a service file prefixed with the user's name
    sfname="${user}_${service}.service"
    sfpath="${dpath}/${sfname}"
    if [ ! -f "${sfpath}" ]; then
        echo "Error: could not find your service file for ${service}."
        echo "Expected to find: ${sfpath}. Exiting."
        exit 1
    fi

    # if present, copy the service file to the correct location
    echo "Service: ${service}"
    systemd_path="/etc/systemd/system"
    systemd_fname="dimrod_${service}.service"
    systemd_fpath="${systemd_path}/${systemd_fname}"
    sudo cp "${sfpath}" "${systemd_fpath}"
    echo " - Copied service file to ${systemd_fpath}."

    # reload the daemon
    sudo systemctl daemon-reload
    echo " - Reloaded systemctl daemon."

    # enable the service
    sudo systemctl enable "${systemd_fname}"
    echo " - Enabled ${systemd_fname}."

    # show the status
    sudo systemctl status "${systemd_fname}"
    echo ""
done

