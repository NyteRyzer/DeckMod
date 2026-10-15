#!/usr/bin/env bash
# Run DeckMod straight from this folder without installing.
cd "$(dirname "${BASH_SOURCE[0]}")" && exec python3 -m deckmod "$@"
