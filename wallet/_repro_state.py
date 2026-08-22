import os
from camoufox.pkgman import INSTALL_DIR
from camoufox.multiversion import CONFIG_FILE, load_config, get_active_path

print("cwd:", os.getcwd())
print("HOME:", os.environ.get("HOME"))
print("XDG_CACHE_HOME:", os.environ.get("XDG_CACHE_HOME"))
print("INSTALL_DIR:", INSTALL_DIR)
print("CONFIG_FILE:", CONFIG_FILE, "exists:", CONFIG_FILE.exists())
print("config:", load_config())
print("active:", get_active_path())
