#!/bin/bash
# Creates a tar archive of the entire set of files within the DImROD repository
# (all source code, config files, databases, git-ignored files, etc.) for
# backup.

root="$(dirname $(dirname $(realpath $0)))"
output_dir="${HOME}"
output_name="dimrod_config_backup_$(date +"%Y-%m-%d_%H-%M-%S").tar.gz"

tar -czf "${output_dir}/${output_name}" \
    --exclude ".venv" \
    --exclude "__pycache__" \
    "${root}"

echo "${output_dir}/${output_name}"

