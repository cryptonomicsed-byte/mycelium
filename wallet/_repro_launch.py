import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

print("cwd:", os.getcwd())
print("HOME:", os.environ.get("HOME"))
print("XDG_CACHE_HOME:", os.environ.get("XDG_CACHE_HOME"))

from camoufox.async_api import AsyncCamoufox
import shumei_solver  # noqa: F401  (same import order as farm script)

from camoufox.pkgman import installed_verstr
print("verstr direct:", installed_verstr())


def launch_opts_sync():
    from camoufox.utils import launch_options
    opts = launch_options({})
    return opts


async def main():
    loop = asyncio.get_event_loop()
    opts = await loop.run_in_executor(None, launch_opts_sync)
    print("launch_options via executor OK, keys:", sorted(opts.keys())[:8])


asyncio.run(main())
print("REPRO_OK")
