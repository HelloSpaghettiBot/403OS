#!/bin/sh
set -eu
board=$(dirname "$0")
support/scripts/genimage.sh -c "$board/genimage.cfg"
