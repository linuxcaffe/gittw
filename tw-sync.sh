#!/bin/bash
# tw-sync: tw wrapper shim — routes `tw sync` to `gittw sync`
exec gittw sync "$@"
