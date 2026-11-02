"""Container entrypoint: started as root (a platform volume such as Railway's is mounted root-owned), give /data to
the service user, then drop to it and run tripwright. Started as that user already (compose sets `user:`), it only
runs tripwright. If the volume cannot be fixed, set RAILWAY_RUN_UID=0 on Railway (the service then runs as root)."""
import os
import sys

UID = GID = 10001
DATA = os.environ.get("TRIPWRIGHT_VOLUME", "/data")


def own(path: str):
    """chown the tree to the service user, skipping what it already owns (a large volume is walked, not rewritten)."""
    for root, folders, files in os.walk(path):
        for name in [root, *(os.path.join(root, item) for item in folders + files)]:
            info = os.lstat(name)
            if (info.st_uid, info.st_gid) != (UID, GID):
                os.lchown(name, UID, GID)


if os.getuid() == 0 and os.environ.get("RAILWAY_RUN_UID") != "0":
    if os.path.isdir(DATA):
        try:
            own(DATA)
            os.chmod(DATA, 0o700)
        except OSError as error:
            print(f"tripwright entrypoint: cannot give {DATA} to UID {UID} ({error}); set RAILWAY_RUN_UID=0 to run as root",
                  file=sys.stderr, flush=True)
    os.setgroups([])
    os.setgid(GID)
    os.setuid(UID)
    os.environ["HOME"] = "/home/tripwright"
os.execvp("tripwright", ["tripwright", *sys.argv[1:]])
