#!/bin/bash
# Double-click this file to install Codex Subscription Router on a Mac.
# It runs install.sh from this folder; the installer asks before installing anything.
cd "$(dirname "$0")" || exit 1
/bin/bash ./install.sh
status=$?
echo
read -n 1 -s -r -p "Press any key to close this window."
exit "${status}"
