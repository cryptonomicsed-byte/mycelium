"""substrate-tunnel -- join any device to a shared Mycelium substrate host
via an autossh-managed reverse SSH tunnel. One command, any device.

BUILT FROM SPEC: the original Fold4 `council-tunnel` script (a
hardcoded-to-that-phone autossh wrapper) wasn't accessible from this build
environment (no SSH path to the phone, and the task explicitly scoped
"don't touch Fold4"). This is a fresh implementation of the same
functional shape described in the orchestration plan -- a generic
"reverse SSH to a configured relay host, port configurable, autossh-
managed" tool -- not a byte-for-byte port. Diff against the real phone
script before treating this as a drop-in replacement on Fold4 itself.

What it does: runs `autossh -M 0 -N -R <remote_port>:localhost:<local_port>
<user>@<host> -i <key> ...` with keepalive options so a lost connection is
detected and retried automatically, without relying on autossh's legacy
monitoring port (-M 0, using SSH's own ServerAliveInterval instead --
simpler firewall story, one less open port).
"""
import argparse
import os
import shutil
import subprocess
import sys

DEFAULT_RELAY_HOST = os.environ.get("SUBSTRATE_RELAY_HOST", "")
DEFAULT_RELAY_USER = os.environ.get("SUBSTRATE_RELAY_USER", "")
DEFAULT_REMOTE_PORT = int(os.environ.get("SUBSTRATE_REMOTE_PORT", "8811"))
DEFAULT_LOCAL_PORT = int(os.environ.get("SUBSTRATE_LOCAL_PORT", "8811"))
DEFAULT_SSH_KEY = os.environ.get("SUBSTRATE_SSH_KEY", "")
DEFAULT_SERVER_ALIVE_INTERVAL = int(os.environ.get("SUBSTRATE_KEEPALIVE_INTERVAL", "15"))
DEFAULT_SERVER_ALIVE_COUNT_MAX = int(os.environ.get("SUBSTRATE_KEEPALIVE_COUNT_MAX", "3"))


def build_command(host: str, user: str, remote_port: int, local_port: int,
                  ssh_key: str, alive_interval: int, alive_count_max: int) -> list:
    target = f"{user}@{host}" if user else host
    cmd = [
        "autossh", "-M", "0", "-N",
        "-R", f"{remote_port}:localhost:{local_port}",
        target,
        "-o", f"ServerAliveInterval={alive_interval}",
        "-o", f"ServerAliveCountMax={alive_count_max}",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if ssh_key:
        cmd += ["-i", ssh_key]
    return cmd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="substrate-tunnel",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--host", default=DEFAULT_RELAY_HOST,
                    help="relay host to tunnel to (env: SUBSTRATE_RELAY_HOST, required)")
    ap.add_argument("--user", default=DEFAULT_RELAY_USER,
                    help="SSH user on the relay host (env: SUBSTRATE_RELAY_USER)")
    ap.add_argument("--remote-port", type=int, default=DEFAULT_REMOTE_PORT,
                    help=f"port to bind on the relay host (env: SUBSTRATE_REMOTE_PORT, default {DEFAULT_REMOTE_PORT})")
    ap.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT,
                    help=f"local port to forward (env: SUBSTRATE_LOCAL_PORT, default {DEFAULT_LOCAL_PORT})")
    ap.add_argument("--ssh-key", default=DEFAULT_SSH_KEY,
                    help="path to SSH private key (env: SUBSTRATE_SSH_KEY)")
    ap.add_argument("--keepalive-interval", type=int, default=DEFAULT_SERVER_ALIVE_INTERVAL)
    ap.add_argument("--keepalive-count-max", type=int, default=DEFAULT_SERVER_ALIVE_COUNT_MAX)
    ap.add_argument("--dry-run", action="store_true", help="print the autossh command, don't run it")
    args = ap.parse_args(argv)

    if not args.host:
        print("substrate-tunnel: --host or SUBSTRATE_RELAY_HOST is required", file=sys.stderr)
        return 2

    cmd = build_command(
        args.host, args.user, args.remote_port, args.local_port,
        args.ssh_key, args.keepalive_interval, args.keepalive_count_max,
    )

    if args.dry_run:
        # No autossh-installed requirement for dry-run -- the point is to
        # let you inspect the command before installing anything.
        print(" ".join(cmd))
        return 0

    if not shutil.which("autossh"):
        print(
            "substrate-tunnel: 'autossh' not found on PATH.\n"
            "  Debian/Ubuntu:  sudo apt install autossh\n"
            "  macOS:          brew install autossh\n"
            "  Termux:         pkg install autossh",
            file=sys.stderr,
        )
        return 1

    print(f"substrate-tunnel: joining {args.host} (remote:{args.remote_port} <- local:{args.local_port})", flush=True)
    try:
        # Foreground exec -- systemd (Restart=always) or the caller's shell
        # owns respawn-on-exit; this process just IS the tunnel.
        os.execvp(cmd[0], cmd)
    except FileNotFoundError:
        print("substrate-tunnel: autossh disappeared from PATH between check and exec", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
