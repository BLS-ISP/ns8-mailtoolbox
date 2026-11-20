#!/bin/bash

set -e

if [[ -z "$1" ]]; then
    echo "Missing parameter: append 'watch' or 'build'"
    exit 1
elif [[ "$1" == "watch" ]]; then
    (yarn install || npm install --legacy-peer-deps) && (yarn watch || npm run watch)
elif [[ "$1" == "build" ]]; then
    (yarn install || npm install --legacy-peer-deps) && (yarn build || npm run build)
else
    echo "Parameter not recognized: '$1'. Only 'watch' or 'build' are allowed"
    exit 1
fi
